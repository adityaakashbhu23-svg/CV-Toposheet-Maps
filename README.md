# CV-Toposheet - Historical Map Digitization Pipeline



Automated extraction of place names and geographic features from Survey of India (and other) topographical maps using Computer Vision, OCR, and multi-LLM post-processing.



![Pipeline: Upload map → OCR → LLM cleaning → Searchable database](https://img.shields.io/badge/status-fully%20operational-brightgreen)



---



## What it does



Upload a scanned topographic map (JPG/PNG/TIF, up to 500 MB) and the pipeline:



1. **Tiles** the map into 1024x1024 px patches (preserving full resolution)

2. **OCR** - Google Cloud Vision (primary), EasyOCR or Tesseract (fallback)

3. **LLM cleaning** - removes noise, fixes spelling, classifies features (settlement / river / mountain / landmark)

4. **Grid detection** - assigns lat/lon coordinates from the map's alphanumeric grid

5. **Web interface** - search, filter, and export all extracted place names as a table



Supports Survey of India sheets, UK OS, USGS, German, French.



---



## Installation (Windows - no Python needed)



Download **CVToposheet_Setup.exe** from [Releases](../../releases) and run it.  

The installer sets up a self-contained app - no Python or dependencies required.



After installing, follow the **API Keys Setup Guide** in the Start Menu to add your own keys.



---



## Running from source



### Requirements



- Python 3.11+

- All packages in `requirements.txt`



```bash

git clone https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps.git

cd CV-Toposheet-Maps

pip install -r requirements.txt

```



### Configuration (required before first run)



**Step 1 - Copy the env template:**



```bash

cp .env.example .env

```



Open `.env` and fill in at least one LLM API key (Groq and Gemini are free):



| Key | Where to get it | Free? |

|-----|----------------|-------|

| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) | ✅ Free |

| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | ✅ Free |

| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Paid |

| `CLAUDE_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Paid |

| `GROK_API_KEY` | [console.x.ai](https://console.x.ai) | Paid |



**Step 2 - Google Cloud Vision service account JSON (strongly recommended for good results):**



> ⚠️ **Without this file, OCR quality will be significantly lower.** The app falls back to EasyOCR which misses many place names, especially on older or faded maps. For any serious use, the GCV service account JSON is essential.



To set it up:



1. Create a [Google Cloud project](https://console.cloud.google.com)

2. Enable the **Cloud Vision API**

3. Go to **IAM & Admin → Service Accounts** → Create a service account

4. Under the service account → **Keys → Add Key → JSON** → download the file

5. Save the downloaded JSON file as `service_account.json` in the project root



Google Cloud Vision offers a **free tier of 1,000 OCR requests/month** - more than enough for most research use.



**Step 3 - Vertex AI - optional (highest LLM accuracy):**



If you want to use Vertex AI (Gemini via Google Cloud):



1. Enable **Vertex AI API** in your Google Cloud project

2. Grant your service account the **Vertex AI User** role

3. Set `LLM_PROVIDER=vertex` and `VERTEX_PROJECT=your-project-id` in `.env`



### Running the app



```bash

python app.py

```



Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.



---



## Files NOT included (add your own)



| File | Purpose |

|------|---------|

| `.env` | Your API keys - copy from `.env.example` |

| `service_account.json` | Google Cloud Vision + Vertex AI credentials |



These are excluded from the repository for security. **Never commit them to git.**



---



## Project structure



```

app.py                  Flask web server + UI

config.py               Loads all settings from .env

1_tile_maps.py          Phase 1 - tiling engine

2_ocr_extraction.py     Phase 2 - OCR (GCV / EasyOCR / Tesseract)

3_grid_detection.py     Phase 3 - grid coordinate detection

4_llm_cleaning.py       Phase 4 - LLM post-processing

5_database_assembly.py  Phase 5 - builds searchable database

6_query_interface.py    Phase 6 - search and export

utils/                  Shared utilities

maps/                   Place your input map images here

results/                Output database (auto-generated)

.env.example            API key template - copy to .env

```



---



## License



See [LICENSE.txt](LICENSE.txt)



---



## About the Developer



**Aditya Akash**  

MPhil in Digital Humanities  

University of Cambridge



Heritage professional specialising in heritage documentation and geospatial analysis. This project applies AI-driven computer vision to the digitization of historical toposheets and cartographic records.



---



## Funding & Acknowledgements



This software was developed as part of an MPhil research project in Digital Humanities at the University of Cambridge.



Funded by:

- **Commonwealth Scholarship Commission (CSC)**

- **Cambridge Trust (CT Trust)**



The developer gratefully acknowledges the support of these organisations in making this research possible.



---



## Accuracy & Disclaimer



**This tool does not claim 100% accuracy.** OCR and LLM outputs are probabilistic - results will always contain some errors, missed names, or misclassifications depending on map quality, scan resolution, handwriting style, and the API tier used.



- Older or faded maps will produce more errors than clean modern scans

- Place names in non-English scripts may not extract correctly

- Always cross-check extracted features against the original source map before using in any research, publication, or GIS project

- Treat all outputs as a first-draft aid, not a definitive record



This software is provided "as is", without warranty of any kind.



Aditya Akash, the University of Cambridge, the Commonwealth Scholarship Commission, and CT Trust are not liable for any data loss, damage, or consequences arising from the use of this software.

