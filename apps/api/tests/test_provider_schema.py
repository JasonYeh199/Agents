from app.schemas import EarningsReport


def test_openai_structured_output_schema_has_no_unsupported_uri_format():
    schema = EarningsReport.model_json_schema()

    def walk(value):
        if isinstance(value, dict):
            assert value.get("format") != "uri"
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
