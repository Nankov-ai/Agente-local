import sys
import json
import requests
from pathlib import Path


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "nodeflow-faturix"


def extract_text_from_pdf(path: Path) -> str:
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    return "\n".join(page.get_text() for page in doc)


def call_faturix(text: str) -> str:
    payload = {
        "model": MODEL_NAME,
        "prompt": f"Analisa este documento:\n\n{text}",
        "stream": False,
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.ConnectionError:
        print("Erro: Ollama não está a correr. Inicia com: ollama serve")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Uso: python faturix.py <ficheiro.pdf>")
        print("Exemplo: python faturix.py Faturas/fatura_abril.pdf")
        sys.exit(1)

    path = Path(sys.argv[1])

    if not path.exists():
        print(f"Ficheiro não encontrado: {path}")
        sys.exit(1)

    if path.suffix.lower() != ".pdf":
        print("Só PDFs são suportados. Formato recebido: " + path.suffix)
        sys.exit(1)

    print(f"A ler {path.name}...")
    text = extract_text_from_pdf(path)

    if not text.strip():
        print("Não foi possível extrair texto do PDF. O ficheiro pode ser um scan.")
        sys.exit(1)

    print("A enviar para o Faturix...")
    result = call_faturix(text)

    try:
        parsed = json.loads(result)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(result)


if __name__ == "__main__":
    main()
