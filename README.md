# Agentic FinSearch

Accurate retrieval of real-time financial data is a core infrastructure for building FinAgents. Our Agentic FinSearch is considered as a cornerstone towards democratizing financial intelligence to the general public. Also, it serves as a reference architecture for safe AI adoption in the financial services industry.

[![Open Financial LLM Leaderboard](https://img.shields.io/badge/FinLLM-Leaderboard-blue)](https://finosfoundation-open-financial-llm-leaderboard.hf.space/#/?columns=isPinned%2Crank%2Cmodel_type%2Cid%2Cmodel.average_score%2Cevaluations.vision_average%2Cevaluations.audio_average%2Cevaluations.english_average%2Cevaluations.chinese_average%2Cevaluations.japanese_average%2Cevaluations.spanish_average%2Cevaluations.greek_average%2Cevaluations.bilingual_average%2Cevaluations.multilingual_average)

- **Air-gapped deployment** - By design, our Agentic FinSearch emphasizes  in the production environment, in order to protect user privacy. 
- **Numerical accuracy** - This is a critical issue, since one cannot directly apply general-purpose LLMs and agents to business and finance.
- **No hallucination & misinformation** - For safe AI adoption in high-stake domains like finance, no hallucination & misinformation is a must.  
- **Source transparency** — every response includes verifiable sources, ensuring reliability and reducing misinformation.

**No trading suggestions. For research and informational purposes only.**

### Screenshots

1. The search agent overlay: drag, resize, and minimize. Provides context-aware information on the user's current page.

   ![image](Docs/source/_static/images/new_gui_1.png)

3. Source verification — check exactly where each piece of information came from.

   ![image](Docs/source/_static/images/new_gui_2.png)


## Installation

### Prerequisites

- **Python 3.12.x** (uv will download it automatically)
- **Node.js 18+**
- **Google Chrome** browser

### 🐳 Quick Start with Docker (Recommended)

Build and run everything with a single command.

```bash
# Clone the repository
git clone https://github.com/Open-Finance-Lab/Agentic-FinSearch.git
cd Agentic-FinSearch

# Copy backend environment template and add your API keys
cp Main/backend/.env.example Main/backend/.env
# Edit Main/backend/.env and add at least one API key (OPENAI_API_KEY, ANTHROPIC_API_KEY, or DEEPSEEK_API_KEY)

# Start everything with one command (first run builds the image)
docker compose up
```

The backend API will be available at http://localhost:8000

### Manual Backend Setup with uv (Optional)

Use this path if you want to run Django without Docker.

```bash
# Clone the repository
git clone https://github.com/Open-Finance-Lab/Agentic-FinSearch.git
cd Agentic-FinSearch

# Install backend dependencies with uv (Python 3.12)
cd Main/backend
uv sync --python 3.12 --frozen

# Install Playwright's Chromium once per machine
uv run playwright install chromium

# Start Django (no database migrations required)
uv run python manage.py runserver
```

For the browser extension, run once per environment:

```bash
cd Main/frontend
bun install
bun run build:full
```
### Post-Installation

1. **Configure API Keys (Required)**
   
   Edit `Main/backend/.env` (copied from `Main/backend/.env.example`) and add at least one:
   ```
   OPENAI_API_KEY=your-actual-openai-key
   ANTHROPIC_API_KEY=your-actual-anthropic-key
   DEEPSEEK_API_KEY=your-actual-deepseek-key
   ```
   
   **Note**: The server will refuse to start without at least one valid API key configured.

2. **Load Browser Extension**
   
   - Open Chrome and navigate to Extensions page → `chrome://extensions`
   - Enable Developer mode
   - Click "Load unpacked"
   - Select `Main/frontend/dist` folder

### Troubleshooting

- **"No API keys configured!"**: The server won't start without valid API keys in `Main/backend/.env`
- **Running locally**: Use `uv sync --python 3.12 --frozen` inside `Main/backend` to bootstrap dependencies.
- **Port 8000 in use**: Close other servers or continue anyway.
- **Non-English systems**: UTF-8 encoding is automatically handled.
- **Playwright browser errors**: If you see browser-related errors, install Chromium:
  ```bash
  cd Main/backend
  uv run playwright install chromium
  ```
  See `Main/backend/PLAYWRIGHT_INTEGRATION.md` for detailed troubleshooting.

## Usage and Documentation

For detailed usage instructions and more information, see: https://agentic-finsearch.readthedocs.io/


### Live Demo

The backend is deployed at [agenticfinsearch.org](https://agenticfinsearch.org).

### Citing

This project was originally published as *FinGPT Search Agents* and has since been renamed to **Agentic FinSearch**.

```
@inproceedings{tian2024customized,
  title={Customized fingpt search agents using foundation models},
  author={Tian, Felix and Byadgi, Ajay and Kim, Daniel S and Zha, Daochen and White, Matt and Xiao, Kairong and Liu, Xiao-Yang},
  booktitle={Proceedings of the 5th ACM International Conference on AI in Finance},
  pages={469--477},
  year={2024}
}
```


**Disclaimer: We are sharing codes for academic purposes under the MIT education license. Nothing herein is financial 
advice, and NOT a recommendation to trade real money. Please use common sense and always first consult a professional
before trading or investing.**
