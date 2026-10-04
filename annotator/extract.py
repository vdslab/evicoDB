#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from annotator/.env if present
script_dir = Path(__file__).resolve().parent
load_dotenv(script_dir / ".env")

MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 10

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig
except ImportError:
    print(
        "Error: google-cloud-aiplatform is not installed. Please run pip install -r requirements.txt inside .venv first.",
        file=sys.stderr,
    )
    sys.exit(1)


# Response schema for Gemini: only what the model reads from the text.
# Run metadata (model, prompt version, source file, token usage) is added by this script.
class Diagnoses(BaseModel):
    diagnosis: str = Field(
        ..., description="Diagnosis name as in the ICD-O coding line (or the heading)"
    )
    icd_o: Optional[str] = Field(
        None, description="ICD-O morphology/behavior code (e.g., 8253/3)"
    )
    major_classifications: Optional[str] = Field(
        None, description="Major classification, e.g., Adenocarcinoma"
    )
    organs: Optional[str] = Field(None, description="Target organ, e.g., Lung")
    # Allowed values follow the ground truth (label/example-label.csv)
    primary_metastasis: Optional[Literal["Primary", "Metastasis"]] = Field(
        None, description="Whether primary or metastasis"
    )
    origin: Optional[
        Literal["Epithelial", "Non-epithelial", "Mixed", "Tumor-like"]
    ] = Field(None, description="Histological origin")
    malignancy: Optional[Literal["Benign", "Malignant", "Various"]] = Field(
        None, description="Benign (ICD-O /0), Malignant (ICD-O /3); otherwise judged from the text"
    )


class Finding(BaseModel):
    method: Optional[str] = Field(
        None, description="Testing method, e.g., IHC, Genetic test"
    )
    molecule_name: str = Field(
        ..., description="Molecule or marker name, e.g., TTF-1, CK7, KRAS"
    )
    result: str = Field(
        ...,
        description="Raw result description, e.g., Positive, Negative, focal positive",
    )
    result_normalized: Optional[
        Literal[
            "positive",
            "negative",
            "focal_positive",
            "rare",
            "positive_subset",
            "altered",
            "equivocal",
        ]
    ] = Field(None, description="Normalized result status")
    evidence_text: str = Field(
        ..., description="Extract of exact text serving as evidence for this finding"
    )
    # Asked for since prompt v4; optional so earlier prompts still validate
    confidence: Optional[float] = Field(
        None, description="How definitely the text states the result (0.0-1.0)"
    )
    applies_to: List[str] = Field(
        ...,
        description="Diagnosis names this finding applies to; empty means all diagnoses in the text",
    )


class ExtractionResult(BaseModel):
    # One input text can cover several diagnoses (one per ICD-O coding line) or none (introductions)
    diagnoses: List[Diagnoses]
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
                null_indices = [
                    i
                    for i, item in enumerate(any_of)
                    if isinstance(item, dict) and item.get("type") == "null"
                ]
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


def run_extraction(
    prompt_path: Path,
    input_path: Path,
    output_path: Path,
    model_name: str,
    prompt_version: str,
):
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
            ),
        )

        raw_output = response.text
        if not raw_output:
            raise ValueError("Empty response received from Vertex AI API.")

        # Parse output to ensure it matches the schema and is valid JSON
        result_json = json.loads(raw_output)

        # Token usage reported by the API
        meta = response.usage_metadata
        usage = {
            "prompt_tokens": getattr(meta, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
            "thinking_tokens": getattr(meta, "thoughts_token_count", 0) or 0,
            "total_tokens": getattr(meta, "total_token_count", 0) or 0,
        }
        print(
            f"Token usage: prompt={usage['prompt_tokens']} output={usage['output_tokens']} "
            f"thinking={usage['thinking_tokens']} total={usage['total_tokens']}"
        )

        # Add run metadata (evaluate.py finds the input text via ocr_sources.source_name)
        result_json = {
            "ocr_sources": {"source_name": input_path.name},
            "extraction_runs": {
                "model_name": model_name,
                "prompt_version": prompt_version,
                "status": "success",
                "token_usage": usage,
            },
            **result_json,
        }

        # Write clean formatted JSON
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_json, f, indent=2, ensure_ascii=False)

        print(f"Successfully processed: {input_path.name} -> {output_path}")
        return usage

    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"Error extracting from {input_path.name}: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Batch extract information from medical texts using Vertex AI (Gemini)"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=str(script_dir / "prompts" / "prompt-v4.txt"),
        help="Path to the prompt template text file",
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=str(script_dir / "input" / "lung"),
        help="Path to a directory of input .txt files, or a single .txt file "
        "(default: input/lung, the chapter covered by the ground truth)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(script_dir / "output"),
        help="Base directory; each run saves JSONs into a new <timestamp>_<prompt_version> subfolder",
    )
    args = parser.parse_args()

    # Check GCP project configuration
    gcp_project = os.environ.get("GCP_PROJECT")
    gcp_location = os.environ.get("GCP_LOCATION")
    model_name = os.environ.get(
        "ANNOTATOR_GEMINI_MODEL", os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    )

    if not gcp_project or not gcp_location:
        print(
            "Error: GCP_PROJECT and GCP_LOCATION must be set as environment variables or in annotator/.env",
            file=sys.stderr,
        )
        print("Please check your .env configuration.", file=sys.stderr)
        sys.exit(1)

    # Initialize Vertex AI SDK
    vertexai.init(project=gcp_project, location=gcp_location)

    prompt_path = Path(args.prompt)
    if not prompt_path.exists():
        print(
            f"Error: Prompt template file '{prompt_path}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = Path(args.input_dir)
    if not input_path.exists():
        print(f"Error: Input path '{input_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Accept either a single .txt file or a directory of .txt files
    if input_path.is_file():
        txt_files = [input_path]
    else:
        txt_files = sorted(list(input_path.glob("*.txt")))
    if not txt_files:
        print(f"No .txt files found in input path: {input_path}")
        return

    print(f"Found {len(txt_files)} text files to process.")
    prompt_version = prompt_path.stem

    # Separate each run into its own folder, e.g. output/20261004-195300_prompt-v2/
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}_{prompt_version}"
    output_dir = Path(args.output_dir) / run_id
    print(f"Output folder: {output_dir}")

    totals = {
        "prompt_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "total_tokens": 0,
    }
    failed = []
    for txt_file in txt_files:
        out_json_file = output_dir / f"{txt_file.stem}.json"
        # Retry transient API errors (rate limits, timeouts) before giving up on a file
        for attempt in range(1, MAX_ATTEMPTS + 1):
            usage = run_extraction(
                prompt_path, txt_file, out_json_file, model_name, prompt_version
            )
            if usage is not None:
                break
            if attempt < MAX_ATTEMPTS:
                wait = RETRY_WAIT_SECONDS * attempt
                print(f"Retrying {txt_file.name} in {wait}s (attempt {attempt + 1}/{MAX_ATTEMPTS})...")
                time.sleep(wait)
        if usage is None:
            failed.append(txt_file)
            continue
        for k in totals:
            totals[k] += usage[k]

    succeeded = len(txt_files) - len(failed)
    print(
        f"\nTotal token usage ({succeeded} files): prompt={totals['prompt_tokens']} "
        f"output={totals['output_tokens']} thinking={totals['thinking_tokens']} total={totals['total_tokens']}"
    )

    if failed:
        print(f"\nFailed {len(failed)}/{len(txt_files)} files:", file=sys.stderr)
        for f in failed:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
