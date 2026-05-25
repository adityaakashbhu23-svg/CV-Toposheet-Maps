# Privacy Policy (CV Toposheet)

**Last updated: May 25, 2026**

## Overview

CV Toposheet ("the App") is a desktop application for digitizing and querying historical topographic maps. This privacy policy explains how the App handles your data.

## Data Collection

CV Toposheet does **not** collect, store, or transmit any personal information. All map processing and database queries are performed locally on your device.

## Third-Party Services

The App optionally uses the following external APIs when configured by the user. All API keys are entered by the user and stored locally on your device. They are never transmitted to any server operated by the developer.

**OCR (map image processing):**
- **Google Cloud Vision API** — sends map image tiles for text recognition. Governed by [Google's Privacy Policy](https://policies.google.com/privacy).

**LLM (text cleaning user's choice of one):**
- **Groq API** - sends extracted map text for correction. Governed by [Groq's Privacy Policy](https://groq.com/privacy-policy/).
- **Google Gemini API** - sends extracted map text for correction. Governed by [Google's Privacy Policy](https://policies.google.com/privacy).
- **OpenAI API** - sends extracted map text for correction. Governed by [OpenAI's Privacy Policy](https://openai.com/policies/privacy-policy).
- **Anthropic Claude API** - sends extracted map text for correction. Governed by [Anthropic's Privacy Policy](https://www.anthropic.com/privacy).
- **xAI Grok API** - sends extracted map text for correction. Governed by [xAI's Privacy Policy](https://x.ai/privacy-policy).

Only the API(s) the user explicitly configures are contacted. No data is sent to any service the user has not set up.

## Local Storage

The App stores the following data locally on your device only:

- Processed map data and OCR results (in a local SQLite database)
- Application configuration and API keys (in a local config file)
- Log files for debugging

None of this data leaves your device unless you explicitly export it.

## No Analytics or Tracking

The App does not include any analytics, crash reporting, advertising SDKs, or tracking of any kind.

## Contact

If you have questions about this privacy policy, please open an issue at:  
https://github.com/adityaakashbhu23-svg/CV-Toposheet-Maps/issues
