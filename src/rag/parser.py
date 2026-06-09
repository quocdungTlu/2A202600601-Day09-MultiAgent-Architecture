from __future__ import annotations


def parse_policy_markdown(markdown_text: str) -> list[dict]:
    chunks: list[dict] = []
    current_h2 = ""
    current_h3: str | None = None
    current_content: list[str] = []

    def flush() -> None:
        if current_h3 and current_h2:
            content = "\n".join(current_content).strip()
            citation = f"{current_h2} > {current_h3}"
            rendered = f"## {current_h2}\n### {current_h3}\n{content}"
            chunks.append({
                "section_h2": current_h2,
                "section_h3": current_h3,
                "citation": citation,
                "rendered_text": rendered,
            })

    for line in markdown_text.splitlines():
        if line.startswith("## "):
            flush()
            current_h2 = line[3:].strip()
            current_h3 = None
            current_content = []
        elif line.startswith("### "):
            flush()
            current_h3 = line[4:].strip()
            current_content = []
        elif current_h3 is not None:
            current_content.append(line)

    flush()
    return chunks
