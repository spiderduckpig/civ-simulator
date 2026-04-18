import os
import re

files_to_patch = [
    "backend/engine/civ.py",
    "backend/engine/city_dev.py",
    "backend/engine/combat.py",
    "backend/engine/diplomacy.py",
    "backend/engine/simulation.py",
    "backend/main.py"
]

targets = ["civ", "city", "war", "army", "a", "b", "c", "w", "att", "defn", "attacker", "defender", "other"]

joined = "|".join(targets)
pattern1 = re.compile(rf"\b({joined})\[['\"]([a-zA-Z_0-9]+)['\"]\]")
pattern2 = re.compile(rf"\b({joined})\.get\(['\"]([a-zA-Z_0-9]+)['\"]\s*,\s*([^)]*)\)")

for f in files_to_patch:
    with open(f, "r", encoding="utf-8") as file:
        content = file.read()
    
    # replace dict access e.g. civ["id"] -> civ.id
    new_content = pattern1.sub(r"\1.\2", content)
    
    # replace e.g. civ.get("xxx", yyy) => getattr(civ, "xxx", yyy)
    new_content = pattern2.sub(r"getattr(\1, '\2', \3)", new_content)
    
    with open(f, "w", encoding="utf-8") as file:
        file.write(new_content)
print("Applied dict-to-class refactor on local file vars")
