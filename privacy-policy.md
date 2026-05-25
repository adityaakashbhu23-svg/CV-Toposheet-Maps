# Privacy Policy for CV Toposheet

**Last updated: May 25, 2026**

## Overview

CV Toposheet ("the App") is a desktop application for digitizing and querying historical topographic maps. This privacy policy explains how the App handles your data.

## Data Collection

CV Toposheet does **not** collect, store, or transmit any personal information. All map processing and database queries are performed locally on your device.

## Third-Party Services

The App optionally uses the following external APIs when configured by the user:

- **Google Cloud Vision API** — used for OCR (optical character recognition) on map images. Data sent is limited to map image tiles. Governed by [Google's Privacy Policy](https://policies.google.com/privacy).
- **Groq AI API** — used for text cleaning and correction. Data sent is limited to extracted map text. Governed by [Groq's Privacy Policy](https://groq.com/privacy-policy/).

These API keys are provided by the user and stored locally in a configuration file on your device. They are never transmitted to any server operated by the developer.

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
