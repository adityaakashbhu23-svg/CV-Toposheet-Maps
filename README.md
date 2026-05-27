# CV-Toposheet

Desktop app for extracting place names and geographic features from historical topographic maps using OCR, computer vision, and LLM-assisted post-processing.

![Status](https://img.shields.io/badge/status-active-brightgreen)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-blue)
![Interface](app_icon.png)

## Overview

CV-Toposheet is designed for researchers, historians, archivists, and heritage professionals working with scanned maps. It turns large raster toposheets into a searchable database of extracted features with model-assisted cleanup and export tools.

Main workflow:

1. Upload one or more scanned maps in JPG, PNG, or TIF format.
2. Tile the map without downscaling the original image.
3. Run OCR using Google Cloud Vision or local fallback engines.
4. Clean and classify extracted text with the selected LLM provider.
5. Assemble results into a searchable local database.
6. Review, filter, compare, and export extracted features.

Supported map families include Survey of India, UK Ordnance Survey, USGS, and similar historical sheet-based map layouts.

## Screenshots And Assets

![App homepage](Images/1%20(2).png)

![App screenshot](Images/1%20(4).png)

Store and build assets are included in the repository for packaging under [_build/Assets](_build/Assets).

## Key Features

- Desktop-first workflow with packaged EXE, MSIX, and macOS build support.
- Multiple model choices including Vertex AI, Groq, Gemini, OpenAI, Claude, Grok, and offline OCR mode.
- Searchable local database for extracted map features.
- Excel and table export support.
- Guided onboarding for API keys and Google Cloud setup.
- In-app AI content reporting flow for user feedback and moderation review.

## Downloads

For Windows, download the packaged installer from [Releases](../../releases):

- `CVToposheet_Setup.exe`
- `CVToposheet_*.msix`

The Windows installer is self-contained and does not require a separate Python installation.

## Quick Start From Source

### Requirements

- Python 3.11+
- Packages from `requirements.txt`

```bash
git clone https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps.git
cd CV-Toposheet-Maps
pip install -r requirements.txt
```

### Required Configuration

1. Copy the environment template:

```bash
cp .env.example .env
```

2. Add at least one LLM API key. Free options are supported:

| Key | Provider | Cost |
| --- | --- | --- |
| `GROQ_API_KEY` | [Groq](https://console.groq.com) | Free tier available |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) | Free tier available |
| `OPENAI_API_KEY` | [OpenAI](https://platform.openai.com/api-keys) | Paid |
| `CLAUDE_API_KEY` | [Anthropic](https://console.anthropic.com) | Paid |
| `GROK_API_KEY` | [xAI](https://console.x.ai) | Paid |

3. Add a Google Cloud Vision service account JSON for high-quality OCR.

Recommended setup:

1. Create a Google Cloud project.
2. Enable `Cloud Vision API`.
3. Create a service account.
4. Generate a JSON key.
5. Upload or save it as `service_account.json`.

Without Google Cloud Vision, the app falls back to EasyOCR, which is slower and less reliable on many historical maps.

4. Optional: enable Vertex AI for the highest-quality LLM path.

### Run The App

```bash
python app.py
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000).

## Privacy And Data Handling

CV-Toposheet is a local desktop application. It does not include analytics, advertising, or background tracking.

Relevant links:

- [Privacy Policy](privacy-policy.md)
- [Repository Issues](https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps/issues)

Data handling summary:

- Map processing results are stored locally.
- API keys are stored locally by the user.
- Only user-configured third-party OCR or LLM services are contacted.
- Users can export their own results at any time.

## Support And Reporting

If you need support or want to report a problem:

- Open a repository issue: [GitHub Issues](https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps/issues)
- Use the in-app `Report AI Content` action to report inappropriate or unsafe generated content.

The in-app report flow creates a reference ID and directs the user to the publisher issue page for review.

## Repository Structure

Core files and folders:

| Path | Purpose |
| --- | --- |
| `app.py` | Flask app and desktop UI HTML templates |
| `config.py` | Loads local environment configuration |
| `1_tile_maps.py` | Phase 1 tiling |
| `2_ocr_extraction.py` | Phase 2 OCR |
| `3_grid_detection.py` | Phase 3 grid detection |
| `4_llm_cleaning.py` | Phase 4 LLM cleanup and classification |
| `5_database_assembly.py` | Phase 5 database generation |
| `6_query_interface.py` | Query and export layer |
| `utils/` | Shared OCR, LLM, and helper utilities |
| `_build/` | Packaging assets, specs, and installer files |
| `maps/` | Input map files |
| `results/` | Generated outputs |

Excluded from version control:

- `.env`
- `service_account.json`
- user-generated outputs and build artifacts

## Packaging

This repository includes packaging support for:

- Windows EXE via PyInstaller
- Windows MSIX packaging
- macOS app and DMG via GitHub Actions on macOS runners

## License

See [LICENSE](LICENSE).

## About The Developer

**Aditya Akash**  
MPhil in Digital Humanities  
University of Cambridge

This project applies AI-assisted computer vision and OCR to the digitization of historical toposheets and related cartographic materials.

## Funding And Acknowledgements

This software was developed as part of an MPhil research project in Digital Humanities at the University of Cambridge.

Supported by:

- Commonwealth Scholarship Commission
- Cambridge Trust

## Accuracy And Disclaimer

This tool does not guarantee perfect extraction accuracy. OCR and LLM outputs are probabilistic and should be reviewed against the original map.

Important limitations:

- Older, faded, or low-resolution maps will produce more OCR errors.
- Non-English scripts may require manual review.
- Feature classification may vary by model and API tier.
- Outputs should be treated as research assistance, not a final authoritative record.

