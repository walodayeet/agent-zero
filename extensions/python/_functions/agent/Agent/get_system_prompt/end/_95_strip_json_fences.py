from helpers import files
from helpers.extension import Extension


class StripJsonFenceMarkers(Extension):
    """Strip JSON fence markers per system section.

    `Agent.prepare_prompt` strips the concatenated system prompt, where one
    unterminated example can pair with a later section's closing fence and swallow
    the content that followed. Stripping each section first keeps an orphan opener
    local to its own section.
    """

    def execute(self, data: dict | None = None, **kwargs):
        if not isinstance(data, dict):
            return
        result = data.get("result")
        if not isinstance(result, list):
            return
        data["result"] = [
            files.remove_code_fences(section, language="json")
            if isinstance(section, str)
            else section
            for section in result
        ]
