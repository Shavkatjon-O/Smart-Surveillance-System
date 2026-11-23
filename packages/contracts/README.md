# Contracts

The shared contracts of Smart Surveillance System. openapi.json is the OpenAPI 3.1 document of the API, generated from the running application and committed so clients can generate code and check compatibility without starting the API. events holds the JSON Schemas of the event bus: envelope.schema.json for the CloudEvents envelope and one schema per versioned event type for its data, such as sss.analytics.event.v1.schema.json; packages/event-bus/README.md describes the subjects and streams. The root README describes the product.

## Run

Regenerate the document after any change to the API routes or schemas:

```
scripts/export-openapi.sh
```

The script imports the API in a throwaway uv container and writes packages/contracts/openapi.json, or the path given as its first argument.

## Test

The API test test_openapi.py checks the document the application produces, and the event bus tests validate an envelope of every event type against its schema. Run the API suite as services/api/README.md describes, then regenerate this file and confirm git shows no unintended difference.

## Configuration

| Item | Default | Meaning |
|---|---|---|
| Output path | packages/contracts/openapi.json | First argument of scripts/export-openapi.sh |
