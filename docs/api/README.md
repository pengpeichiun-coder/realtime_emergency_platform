# OpenAPI snapshot

`openapi-v1.0.0.json` is generated from the FastAPI route and Pydantic models. Do not edit the JSON by hand or maintain a second API schema. After changing the API contract, run `make api-docs`, review the generated diff, and run `make api-docs-check`. The exporter checks the OpenAPI 3.1 structure, required paths, response definitions, and every local JSON Pointer reference.

The live interactive Swagger UI is available at `/docs` when the local API is running. The snapshot records the portfolio release's API surface; it is not a separate promise of long-term compatibility.
