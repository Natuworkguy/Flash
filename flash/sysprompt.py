import os


def get_system_prompt():
    """Get system prompt for the AI model."""

    with open(
        os.path.join(
            os.path.dirname(__file__),
            "system_prompt.txt"
        ),
        "r"
    ) as f:
        return f.read().strip()
