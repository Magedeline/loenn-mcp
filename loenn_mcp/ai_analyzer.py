"""
ai_analyzer.py — Claude API-powered map analysis for loenn-mcp

Provides AI-powered analysis and suggestions for Celeste maps using
Anthropic's Claude API. Requires ANTHROPIC_API_KEY environment variable.

Features:
  - AI map analysis and improvement suggestions
  - Room narrative/description generation
  - Entity placement recommendations
  - Map theme/style analysis
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lazy import anthropic to avoid import errors when not installed
def _get_anthropic_client():
    """Get Anthropic client, raising error if API key not set."""
    try:
        from anthropic import Anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package not installed. "
            "Install with: pip install anthropic>=0.40.0"
        )
    
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Set it to your Claude API key from console.anthropic.com"
        )
    
    return Anthropic(api_key=api_key)


DEFAULT_MODEL = "claude-3-5-sonnet-20241022"


def _build_map_context(map_data: dict, workspace: Path) -> Dict[str, Any]:
    """Build a context summary of the map for AI analysis."""
    # Import here to avoid circular imports
    try:
        from . import celeste_bin as cb
    except ImportError:
        import celeste_bin as cb
    
    rooms = cb.get_rooms(map_data)
    
    room_summaries = []
    total_entities = 0
    total_triggers = 0
    entity_types = set()
    
    for room in rooms:
        name = room.get("name", "?")
        width = room.get("width", 0)
        height = room.get("height", 0)
        
        ent_el = cb.find_child(room, "entities")
        entities = ent_el.get("__children", []) if ent_el else []
        
        trig_el = cb.find_child(room, "triggers")
        triggers = trig_el.get("__children", []) if trig_el else []
        
        total_entities += len(entities)
        total_triggers += len(triggers)
        
        room_entity_types = set()
        for e in entities:
            etype = e.get("__name", "?")
            entity_types.add(etype)
            room_entity_types.add(etype)
        
        room_summaries.append({
            "name": name,
            "size": f"{width}x{height}",
            "entity_count": len(entities),
            "trigger_count": len(triggers),
            "entity_types": list(room_entity_types),
            "dark": room.get("dark", False),
            "space": room.get("space", False),
            "music": room.get("music", ""),
            "wind": room.get("windPattern", "None"),
        })
    
    return {
        "package": map_data.get("_package", "?"),
        "room_count": len(rooms),
        "total_entities": total_entities,
        "total_triggers": total_triggers,
        "entity_types": list(entity_types),
        "rooms": room_summaries,
    }


def ai_analyze_map(
    path: Path,
    workspace: Path,
    analysis_type: str = "general",
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyze a map using Claude API and return AI-powered suggestions.
    
    Args:
        path: Path to the .bin map file
        workspace: Workspace root path
        analysis_type: Type of analysis - "general", "difficulty", "visual", "flow"
        model: Claude model to use
    """
    try:
        client = _get_anthropic_client()
    except RuntimeError as e:
        return f"Claude API Error: {e}"
    
    try:
        from . import celeste_bin as cb
    except ImportError:
        import celeste_bin as cb
    
    map_data = cb.read_map(path)
    context = _build_map_context(map_data, workspace)
    
    # Build analysis prompt based on type
    analysis_prompts = {
        "general": """Analyze this Celeste map and provide:
1. Overall assessment of map structure and design
2. Key strengths of the current design
3. 3-5 specific improvement suggestions
4. Any potential gameplay issues or concerns

Be constructive and specific in your feedback.""",
        
        "difficulty": """Analyze the difficulty curve and gameplay of this Celeste map:
1. Assess the difficulty progression across rooms
2. Identify any difficulty spikes or drops
3. Suggest balancing improvements
4. Comment on checkpoint and strawberry placement
5. Recommend entity adjustments for better flow

Focus on gameplay feel and challenge balance.""",
        
        "visual": """Analyze the visual design of this Celeste map:
1. Assess the visual variety and theme consistency
2. Comment on room sizes and layout variety
3. Suggest visual improvements (lighting, effects, decals)
4. Recommend styleground usage
5. Identify any visual monotony issues

Focus on aesthetics and visual storytelling.""",
        
        "flow": """Analyze the gameplay flow of this Celeste map:
1. Assess how rooms connect and transition
2. Identify potential player confusion points
3. Suggest improvements for navigation clarity
4. Comment on the overall player journey
5. Recommend trigger/entity placement for better guidance

Focus on player experience and movement flow.""",
    }
    
    prompt_text = analysis_prompts.get(analysis_type, analysis_prompts["general"])
    
    system_prompt = """You are an expert Celeste map designer and level design critic. 
You analyze Celeste .bin map files and provide constructive, specific feedback to help creators improve their maps.

Be thorough but concise. Focus on actionable suggestions. Use your knowledge of:
- Celeste's movement mechanics (dash, climb, jump, wallbounce)
- Level design principles (difficulty curves, teaching through play, flow)
- Celeste modding best practices
- Entity and trigger usage patterns"""

    user_prompt = f"""Map file: {path.name}
Package: {context['package']}

Map Summary:
- Rooms: {context['room_count']}
- Total Entities: {context['total_entities']}
- Total Triggers: {context['total_triggers']}
- Entity Types Used: {', '.join(context['entity_types'])}

Room Details:
{json.dumps(context['rooms'], indent=2)}

{prompt_text}"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return f"""AI Map Analysis ({analysis_type})
Model: {model}
Map: {path.name}

{response.content[0].text}"""
    except Exception as e:
        return f"Claude API Error: {e}"


def ai_generate_room_description(
    room_data: dict,
    map_package: str,
    style: str = "atmospheric",
    model: str = DEFAULT_MODEL,
) -> str:
    """Generate a narrative description of a room using Claude.
    
    Args:
        room_data: Room data dict
        map_package: Map package name for context
        style: Description style - "atmospheric", "technical", "story", "brief"
        model: Claude model to use
    """
    try:
        client = _get_anthropic_client()
    except RuntimeError as e:
        return f"Claude API Error: {e}"
    
    # Extract room info
    name = room_data.get("name", "?")
    width = room_data.get("width", 0)
    height = room_data.get("height", 0)
    dark = room_data.get("dark", False)
    space = room_data.get("space", False)
    music = room_data.get("music", "")
    wind = room_data.get("windPattern", "None")
    
    # Get entities
    try:
        from . import celeste_bin as cb
    except ImportError:
        import celeste_bin as cb
    
    ent_el = cb.find_child(room_data, "entities")
    entities = ent_el.get("__children", []) if ent_el else []
    entity_list = [e.get("__name", "?") for e in entities]
    
    style_prompts = {
        "atmospheric": "Write an atmospheric, evocative description of this room as if describing it to a player. Capture the mood and feel.",
        "technical": "Write a technical description of this room's gameplay elements and what challenges it presents to the player.",
        "story": "Write a brief narrative/story snippet that could accompany this room, suggesting what happened here or its significance.",
        "brief": "Write a concise 1-2 sentence description of this room's purpose and character.",
    }
    
    prompt_text = style_prompts.get(style, style_prompts["atmospheric"])
    
    system_prompt = """You are a creative writer specializing in video game level descriptions.
You write evocative descriptions of Celeste map rooms that capture their atmosphere, challenge, and character.
Keep descriptions concise but vivid."""

    user_prompt = f"""Map: {map_package}
Room: {name}
Size: {width}x{height}px
Properties: dark={dark}, space={space}, music={music}, wind={wind}
Entities: {', '.join(entity_list[:20])}{'...' if len(entity_list) > 20 else ''}

{prompt_text}"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return f"""Room Description ({style}): {name}

{response.content[0].text}"""
    except Exception as e:
        return f"Claude API Error: {e}"


def ai_suggest_entities(
    path: Path,
    workspace: Path,
    room_name: str,
    goal: str = "improve_flow",
    model: str = DEFAULT_MODEL,
) -> str:
    """Get AI suggestions for entity placement in a specific room.
    
    Args:
        path: Path to the .bin map file
        workspace: Workspace root path
        room_name: Name of the room to analyze
        goal: Suggestion goal - "improve_flow", "add_challenge", "reduce_difficulty", "add_secrets"
        model: Claude model to use
    """
    try:
        client = _get_anthropic_client()
    except RuntimeError as e:
        return f"Claude API Error: {e}"
    
    try:
        from . import celeste_bin as cb
    except ImportError:
        import celeste_bin as cb
    
    map_data = cb.read_map(path)
    room = cb.get_room(map_data, room_name)
    
    if room is None:
        return f"Room '{room_name}' not found."
    
    # Get room details
    width = room.get("width", 0)
    height = room.get("height", 0)
    
    ent_el = cb.find_child(room, "entities")
    entities = ent_el.get("__children", []) if ent_el else []
    entity_summary = []
    for e in entities:
        entity_summary.append({
            "type": e.get("__name", "?"),
            "x": e.get("x", 0),
            "y": e.get("y", 0),
        })
    
    # Get tile info
    solids = cb.find_child(room, "solids")
    tile_preview = ""
    if solids:
        tiles = solids.get("innerText", "")
        tile_preview = "\n".join(tiles.split("\n")[:10])
    
    goal_prompts = {
        "improve_flow": "Suggest entity placements to improve the movement flow and reduce player confusion. Focus on guiding the player naturally.",
        "add_challenge": "Suggest challenging entity placements that test the player's skills appropriately. Focus on engaging gameplay.",
        "reduce_difficulty": "Suggest modifications to reduce difficulty while keeping the room engaging. Focus on accessibility.",
        "add_secrets": "Suggest secret or optional content placements. Focus on rewarding exploration.",
    }
    
    prompt_text = goal_prompts.get(goal, goal_prompts["improve_flow"])
    
    system_prompt = """You are an expert Celeste level designer specializing in entity placement.
You provide specific, actionable suggestions for entity placement using coordinates.
Always include specific (x, y) coordinates for your suggestions.
Be practical and consider Celeste's movement mechanics."""

    user_prompt = f"""Map: {path.stem}
Room: {room_name} ({width}x{height}px)

Current Entities:
{json.dumps(entity_summary, indent=2)}

Tile Preview (first 10 rows):
{tile_preview}

{prompt_text}

Provide 3-5 specific entity placement suggestions with coordinates and brief explanations."""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return f"""Entity Suggestions for {room_name} ({goal})

{response.content[0].text}"""
    except Exception as e:
        return f"Claude API Error: {e}"
