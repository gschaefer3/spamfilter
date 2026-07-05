import json
import urllib.parse
import urllib.request


def verify_model_available(model: str, generate_url: str, timeout: int = 10) -> None:
    """Raise RuntimeError if Ollama is unreachable or the model isn't pulled.

    Prevents the failure mode where a bad/mistyped model name causes every
    single email to silently classify as HAM via the fail-open error path.
    """
    parsed = urllib.parse.urlparse(generate_url)
    tags_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"

    try:
        with urllib.request.urlopen(tags_url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Could not reach Ollama at {tags_url}: {e}") from e

    available = [m.get("name") or m.get("model") for m in data.get("models", [])]
    if model not in available:
        raise RuntimeError(
            f"Model '{model}' is not available in Ollama. "
            f"Available models: {available or '(none pulled)'}. "
            f"Update ollama.model in your config or run `ollama pull {model}`."
        )


def classify_email(email_body: str, model: str, url: str, timeout: int = 30) -> tuple[bool, str]:
    """Ask the local Ollama model whether an email is spam.

    Returns (is_spam, reason). On any failure, fails open (is_spam=False)
    so a transient Ollama hiccup never causes legitimate mail to be moved.
    """
    truncated_body = email_body[:2000]

    prompt = f"""
    You are an advanced, ruthless email security filter. Analyze the email text below.
    Identify indicators of spam, mass cold-outreach, phishing, or generic marketing.

    Respond STRICTLY in the following raw JSON format, with no extra text or markdown codeblocks:
    {{"is_spam": true, "reason": "Brief reason here"}}
    or
    {{"is_spam": false, "reason": "Legitimate communication"}}

    Email Content:
    \"\"\"
    {truncated_body}
    \"\"\"
    """

    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            output_text = res_data.get("response", "").strip()
            result = json.loads(output_text)
            return bool(result.get("is_spam", False)), result.get("reason", "")
    except Exception as e:
        return False, f"classifier_error: {e}"
