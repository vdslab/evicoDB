#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from annotator/.env if present
script_dir = Path(__file__).resolve().parent
load_dotenv(script_dir / ".env")

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig
except ImportError:
    print("Error: google-cloud-aiplatform is not installed. Please run pip install -r requirements.txt inside .venv first.", file=sys.stderr)
    sys.exit(1)

# Define Pydantic models for structured output matching PROMPT-EXAMPLE.md schema
class OcrSources(BaseModel):
    source_name: Optional[str] = Field(None, description="Name of the source document/file")
    page_no: Optional[int] = Field(None, description="Page number")
    chunk_index: Optional[int] = Field(None, description="Index of the chunk")
    raw_text: str = Field(..., description="The raw, unedited text of the chunk")
    cleaned_text: Optional[str] = Field(None, description="Cleaned or normalized text if applicable")

class ExtractionRuns(BaseModel):
    model_name: str = Field(..., description="Name of the AI model used for extraction")
    prompt_version: str = Field(..., description="Version identifier of the prompt used")
    status: str = Field(..., description="Execution status, e.g., 'success' or 'failed'")

class Bibliography(BaseModel):
    bibliography_text: Optional[str] = Field(None, description="Literature reference text (Journal, Volume, Page, Year)")
    bibliography_url: Optional[str] = Field(None, description="URL of the reference, e.g., PubMed link")
    pmid: Optional[str] = Field(None, description="PubMed ID")
    doi: Optional[str] = Field(None, description="Digital Object Identifier")
    normalized_key: Optional[str] = Field(None, description="Normalized citation key")

class Diagnoses(BaseModel):
    diagnosis: Optional[str] = Field(None, description="Diagnosis name in English or Japanese")
    diagnosis_normalized: Optional[str] = Field(None, description="Standardized/normalized diagnosis name")
    icd_o: Optional[str] = Field(None, description="ICD-O morphology/behavior code (e.g., 8253/3)")
    major_classifications: Optional[str] = Field(None, description="Major classification, e.g., Adenocarcinoma")
    organs: Optional[str] = Field(None, description="Target organ, e.g., Lung")
    primary_metastasis: Optional[str] = Field(None, description="Whether primary or metastasis")
    origin: Optional[str] = Field(None, description="Histological origin, e.g., Epithelial")
    malignancy: Optional[str] = Field(None, description="Malignancy grade, e.g., malignant, benign")
    synonyms: Optional[str] = Field(None, description="Synonyms or related terms")

class Finding(BaseModel):
    method: Optional[str] = Field(None, description="Testing method, e.g., IHC, Genetic test")
    molecule_name: Optional[str] = Field(None, description="Molecule or marker name, e.g., TTF-1, CK7, KRAS mutation")
    molecule_description: Optional[str] = Field(None, description="Description or role of the molecule")
    result: Optional[str] = Field(None, description="Raw result description, e.g., Positive, Negative, focal positive")
    result_normalized: Optional[Literal[
        "positive", "negative", "focal_positive", "rare", "positive_subset", "described_not_specific"
    ]] = Field(None, description="Normalized result status")
    evidence_text: str = Field(..., description="Extract of exact text serving as evidence for this finding")
    evidence_location: Optional[str] = Field(None, description="Section or location in text where evidence was found")
    confidence: Optional[float] = Field(None, description="Confidence score between 0.0 and 1.0")
    review_status: str = Field("unreviewed", description="Status of human review, default 'unreviewed'")
    photo: Optional[str] = Field(None, description="Associated image filename")

class ExtractionResult(BaseModel):
    ocr_sources: OcrSources
    extraction_runs: ExtractionRuns
    bibliography: Optional[Bibliography] = None
    diagnoses: Diagnoses
    findings: List[Finding]

def get_open_api_schema(model_class) -> dict:
    """Convert a Pydantic model to a raw OpenAPI-compatible schema dict resolving local references ($defs) and nullable types."""
    schema = model_class.model_json_schema()
    defs = schema.pop("$defs", {})
    
    def clean_schema(node):
        if isinstance(node, dict):
            # 1. Resolve refs first
            if "$ref" in node:
                ref_path = node["$ref"]
                ref_key = ref_path.split("/")[-1]
                if ref_key in defs:
                    resolved = defs[ref_key].copy()
                    return clean_schema(resolved)
            
            # 2. Convert anyOf containing null type to nullable field
            if "anyOf" in node:
                any_of = node["anyOf"]
                # Look for a null type in anyOf
                null_indices = [i for i, item in enumerate(any_of) if isinstance(item, dict) and item.get("type") == "null"]
                if len(null_indices) == 1 and len(any_of) == 2:
                    null_idx = null_indices[0]
                    other_idx = 1 - null_idx
                    other_item = any_of[other_idx]
                    
                    cleaned_other = clean_schema(other_item)
                    if isinstance(cleaned_other, dict):
                        merged = cleaned_other.copy()
                        merged["nullable"] = True
                        return merged
            
            # 3. Recursively process all properties
            return {k: clean_schema(v) for k, v in node.items()}
            
        elif isinstance(node, list):
            return [clean_schema(item) for item in node]
        return node
        
    return clean_schema(schema)

def run_extraction(prompt_path: Path, input_path: Path, output_path: Path, model_name: str, prompt_version: str):
    # Read prompt template and replace placeholder
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_tmpl = f.read()
    
    with open(input_path, "r", encoding="utf-8") as f:
        input_text = f.read()
    
    prompt = prompt_tmpl.replace("{{INPUT_TEXT}}", input_text)
    
    # Initialize Vertex AI Model
    print(f"Calling Gemini model '{model_name}' on '{input_path.name}'...")
    model = GenerativeModel(model_name)
    
    try:
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                response_schema=get_open_api_schema(ExtractionResult),
                temperature=0.1,  # Low temperature for extraction accuracy
            )
        )
        
        raw_output = response.text
        if not raw_output:
            raise ValueError("Empty response received from Vertex AI API.")
        
        # Parse output to ensure it matches the schema and is valid JSON
        result_json = json.loads(raw_output)
        
        # Inject current run metadata
        if "extraction_runs" in result_json:
            result_json["extraction_runs"]["model_name"] = model_name
            result_json["extraction_runs"]["prompt_version"] = prompt_version
            result_json["extraction_runs"]["status"] = "success"
            
        if "ocr_sources" in result_json:
            result_json["ocr_sources"]["source_name"] = input_path.name
            result_json["ocr_sources"]["raw_text"] = input_text[:500] + ("..." if len(input_text) > 500 else "")
        
        # Write clean formatted JSON
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_json, f, indent=2, ensure_ascii=False)
            
        print(f"Successfully processed: {input_path.name} -> {output_path}")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error extracting from {input_path.name}: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Batch extract information from medical texts using Vertex AI (Gemini)")
    parser.add_argument(
        "--prompt", 
        type=str, 
        default=str(script_dir / "prompts" / "prompt-example.txt"),
        help="Path to the prompt template text file"
    )
    parser.add_argument(
        "--input-dir", 
        type=str, 
        default=str(script_dir / "input"),
        help="Path to directory containing input .txt files"
    )
    parser.add_argument(
        "--output-dir", 
        type=str, 
        default=str(script_dir / "output"),
        help="Path to directory where extracted JSONs will be saved"
    )
    args = parser.parse_args()
    
    # Check GCP project configuration
    gcp_project = os.environ.get("GCP_PROJECT")
    gcp_location = os.environ.get("GCP_LOCATION")
    model_name = os.environ.get("ANNOTATOR_GEMINI_MODEL", os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"))
    
    if not gcp_project or not gcp_location:
        print("Error: GCP_PROJECT and GCP_LOCATION must be set as environment variables or in annotator/.env", file=sys.stderr)
        print("Please check your .env configuration.", file=sys.stderr)
        sys.exit(1)
        
    # Initialize Vertex AI SDK
    vertexai.init(project=gcp_project, location=gcp_location)
    
    prompt_path = Path(args.prompt)
    if not prompt_path.exists():
        print(f"Error: Prompt template file '{prompt_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
        
    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Error: Input directory '{input_dir}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)
        
    output_dir = Path(args.output_dir)
    
    # Get all .txt files
    txt_files = sorted(list(input_dir.glob("*.txt")))
    if not txt_files:
        print(f"No .txt files found in input directory: {input_dir}")
        return
        
    print(f"Found {len(txt_files)} text files to process.")
    prompt_version = prompt_path.stem
    
    for txt_file in txt_files:
        out_json_file = output_dir / f"{txt_file.stem}.json"
        run_extraction(prompt_path, txt_file, out_json_file, model_name, prompt_version)

if __name__ == "__main__":
    main()
