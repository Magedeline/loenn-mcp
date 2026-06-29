# loenn-mcp v5.1.0 — AI-Powered Analysis

**Celeste map editing meets Claude AI.** This release adds intelligent design assistance powered by Anthropic's Claude API, giving you AI feedback on your maps, narrative room descriptions, and smart entity placement suggestions.

---

## ✨ What's New

### 3 New AI-Powered MCP Tools

| Tool | What It Does |
|------|--------------|
| `ai_analyze_map` | Get Claude's expert feedback on your map's design, difficulty curve, visuals, or flow |
| `ai_describe_room` | Generate atmospheric, technical, story, or brief descriptions of any room |
| `ai_suggest_entities` | Receive specific entity placement recommendations with exact coordinates |

---

## 🤖 AI Analysis in Action

### Analyze Your Map Design
```python
# General design assessment
ai_analyze_map(map_path="Maps/MyMod/1-City.bin", analysis_type="general")
# → "Strengths: Good checkpoint distribution. Suggestions: Add more 
#     strawberries in rooms 3-5, consider a difficulty ramp before the boss..."

# Difficulty curve analysis
ai_analyze_map(map_path="Maps/MyMod/1-City.bin", analysis_type="difficulty")
# → "Room a-01: Easy (2/10). Room a-05: Hard spike (8/10). Consider 
#     adding a checkpoint between a-04 and a-05..."

# Visual design feedback
ai_analyze_map(map_path="Maps/MyMod/1-City.bin", analysis_type="visual")
# → "Good use of dark rooms for atmosphere. Consider varying room sizes
#     more — 70% of rooms are 320x184px..."
```

### Generate Room Descriptions
```python
# Atmospheric narrative
ai_describe_room(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", style="atmospheric")
# → "A windswept precipice where ancient stone meets howling gales. 
#     The path forward hangs suspended over void, each jump a prayer 
#     to the mountain's indifferent heart."

# Technical breakdown
ai_describe_room(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", style="technical")
# → "Vertical climb section with 4 wallbounce opportunities. 
#     Spike hazards on alternating sides create timing challenge. 
#     Refill crystal at midpoint prevents softlock."

# Story snippet
ai_describe_room(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", style="story")
# → "Madeline found the old climber's note here: 'The mountain 
#     doesn't care if you're ready.' She almost turned back."
```

### Get Entity Placement Suggestions
```python
# Add challenge
ai_suggest_entities(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", goal="add_challenge")
# → "1. Add trigger spikes at (120, 80) - forces precise timing
#     2. Replace refill at (200, 100) with double-dash variant
#     3. Add crumble block platform at (160, 120) - no return"

# Improve flow
ai_suggest_entities(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", goal="improve_flow")
# → "1. Add jump-through at (80, 140) - clearer upward path
#     2. Place coin at (240, 80) - rewards risky jump
#     3. Add feather to bypass difficult section (optional path)"

# Add secrets
ai_suggest_entities(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", goal="add_secrets")
# → "1. Hidden strawberry behind fake wall at (300, 160)
#     2. Secret room access via spring at (40, 100) - requires 
#        dash-through-spring tech"
```

---

## ⚙️ Setup

### 1. Install the update
```bash
pip install -U loenn-mcp
```

### 2. Get your Claude API key
- Visit [console.anthropic.com](https://console.anthropic.com/)
- Sign up / log in
- Create an API key

### 3. Configure your environment
```bash
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY="sk-ant-api03-..."

# Windows (Command Prompt)
set ANTHROPIC_API_KEY=sk-ant-api03-...

# macOS/Linux
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

### 4. Add to VS Code / Claude Desktop config
```json
{
  "mcpServers": {
    "loenn-mcp": {
      "command": "python",
      "args": ["-m", "loenn_mcp.server"],
      "env": {
        "LOENN_MCP_WORKSPACE": "/path/to/your/mod",
        "ANTHROPIC_API_KEY": "sk-ant-api03-..."
      }
    }
  }
}
```

---

## 🛡️ Graceful Degradation

Don't have an API key? No problem. The AI tools will return helpful error messages explaining what's needed:

```
Claude API Error: ANTHROPIC_API_KEY environment variable not set. 
Set it to your Claude API key from console.anthropic.com
```

All existing 60+ tools continue to work exactly as before.

---

## 📊 Tool Count

**Total MCP tools: 63**

- Map Reading: 8 tools
- Map Editing: 14 tools  
- Room Settings: 5 tools
- Map Metadata: 4 tools
- Stylegrounds: 4 tools
- Decals: 3 tools
- Entity Catalog: 6 tools
- Analysis: 12 tools
- Wiki/Cache: 4 tools
- Mod Project: 2 tools
- Import/Export: 2 tools
- Diff & Fix: 2 tools
- Procedural Generation: 4 tools
- Image/Terrain: 3 tools
- **AI Analysis: 3 tools** ← NEW!
- Lönn Manager: 2 tools

---

## 📝 Technical Details

- **New dependency:** `anthropic>=0.40.0` (optional — only needed for AI features)
- **Default model:** `claude-3-5-sonnet-20241022` (configurable per-call)
- **New module:** `loenn_mcp/ai_analyzer.py` — handles all Claude API interactions
- **Token limits:** Map analysis up to 2000 tokens, room descriptions up to 500 tokens

---

## 🙏 Credits

AI analysis powered by [Anthropic Claude](https://www.anthropic.com/claude). Built for the Celeste modding community.

---

**Full Changelog**: Compare with v5.0.0
