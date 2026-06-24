---
name: vertex_ai_pydantic_schema
description: Convert Pydantic V2 models to OpenAPI schemas compatible with Google Vertex AI response_schema.
---

# Vertex AI Pydantic V2 Schema Converter

Use the following helper function when you need to pass a Pydantic V2 model to Google Vertex AI's `GenerativeModel.generate_content` via the `response_schema` parameter.

## Implementation Helper

```python
def get_open_api_schema(model_class) -> dict:
    """
    Convert a Pydantic V2 model class to a raw OpenAPI-compatible schema dict
    resolving local references ($defs) and nullable types for Vertex AI compatibility.
    """
    schema = model_class.model_json_schema()
    defs = schema.pop("$defs", {})
    
    def clean_schema(node):
        if isinstance(node, dict):
            # 1. Resolve local refs recursively
            if "$ref" in node:
                ref_path = node["$ref"]
                ref_key = ref_path.split("/")[-1]
                if ref_key in defs:
                    resolved = defs[ref_key].copy()
                    return clean_schema(resolved)
            
            # 2. Convert Pydantic V2 anyOf [type, null] to OpenAPI "nullable": True
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
            
            # 3. Recursively process all dictionary properties
            return {k: clean_schema(v) for k, v in node.items()}
            
        elif isinstance(node, list):
            return [clean_schema(item) for item in node]
        return node
        
    return clean_schema(schema)
```

## Example Usage

```python
from vertexai.generative_models import GenerativeModel, GenerationConfig

model = GenerativeModel("gemini-1.5-flash")
schema_dict = get_open_api_schema(MyPydanticModel)

response = model.generate_content(
    prompt,
    generation_config=GenerationConfig(
        response_mime_type="application/json",
        response_schema=schema_dict,
        temperature=0.1
    )
)
```
