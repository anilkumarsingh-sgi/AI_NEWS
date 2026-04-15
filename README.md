# AI NEWS — Motor Insurance Accident Data Extractor

Local LLM-powered (Ollama) system that scrapes Indian news articles and extracts structured road accident data in JSON format. Supports **Hindi, English, Hinglish, Tamil, Telugu, Bengali, Marathi, Gujarati**, and more.

---

## Project Structure

```
AI_NEWS/
├── config.py          # Ollama & app settings
├── prompts.py         # System prompt for the LLM
├── ollama_client.py   # Ollama API wrapper
├── scraper.py         # Web scraper for news URLs
├── processor.py       # Core extraction + validation logic
├── main.py            # CLI entry point
├── requirements.txt   # Python dependencies
└── output/            # Default output directory
```

---

## Prerequisites

1. **Python 3.10+**
2. **Ollama** installed & running — https://ollama.com
3. A model pulled locally:
   ```bash
   ollama pull llama3
   ```

---

## Setup

```bash
cd AI_NEWS
pip install -r requirements.txt
```

Edit `config.py` to change the model or Ollama URL if needed:
```python
OLLAMA_MODEL = "llama3"       # or mistral, gemma2, etc.
OLLAMA_BASE_URL = "http://localhost:11434"
```

---

## Usage

### 1. Process a news URL
```bash
python main.py --url "https://hindi.news18.com/news/rajasthan/jaipur-accident-..."
```

### 2. Process raw text directly
```bash
python main.py --text "ट्रक और कार की टक्कर में 2 लोगों की मौत हो गई और 3 घायल हुए। यह हादसा जयपुर के पास NH-48 पर हुआ।"
```

### 3. Process a text file
```bash
python main.py --file news_article.txt
```

### 4. Batch process multiple URLs
```bash
# urls.txt — one URL per line
python main.py --batch urls.txt --output output/results.json
```

### 5. Choose a different model
```bash
python main.py --url "..." --model mistral
```

---

## Output Example

```json
[
  {
    "accident": true,
    "location": "NH-48 near Jaipur",
    "city": "Jaipur",
    "district": "Jaipur",
    "state": "Rajasthan",
    "police_station": null,
    "vehicle_number": [],
    "vehicle_type": ["Truck", "Car"],
    "persons": [],
    "fatalities": 2,
    "injuries": 3,
    "date": null,
    "time": null,
    "language_detected": "Hindi",
    "source": "news",
    "raw_text": "ट्रक और कार की टक्कर में 2 लोगों की मौत...",
    "confidence_score": 0.93
  }
]
```

---

## Architecture

```
News URL / Text
      │
      ▼
┌─────────────┐
│  scraper.py  │  ← Fetch & clean HTML
└─────┬───────┘
      │ raw text
      ▼
┌──────────────────┐
│  ollama_client.py │  ← System prompt + Ollama /api/chat
└─────┬────────────┘
      │ JSON string
      ▼
┌───────────────┐
│  processor.py  │  ← Validate, normalize, sanitize
└─────┬─────────┘
      │ clean records
      ▼
   JSON Output
```
