import os
import re

keys = [
    'id', 'name', 'leader', 'onom', 'color', 'capital', 'territory', 'cities',
    'population', 'military', 'gold', 'food', 'tech', 'culture', 'age', 'alive',
    'integrity', 'aggressiveness', 'relations', 'allies', 'power', '_settle_score',
    '_target_settle_cell', 'cell', 'is_capital', 'founded', 'trade', 'wealth',
    'focus', 'near_river', 'coastal', 'food_production', 'carrying_cap', 'tiles',
    'farm_tiles', 'hp', 'max_hp', 'last_dmg_tick'
]

files_to_process = []
for root, _, files in os.walk('backend'):
    for f in files:
        if f.endswith('.py') and f not in ('models.py', 'refactor.py'):
            files_to_process.append(os.path.join(root, f))

for p in files_to_process:
    with open(p, 'r', encoding='utf-8') as file:
        content = file.read()
    
    for k in keys:
        # replace ["key"] with .key
        content = re.sub(r'\["' + k + r'"\]', f'.{k}', content)
        # replace ['key'] with .key
        content = re.sub(r"\['" + k + r"'\]", f'.{k}', content)
        
    with open(p, 'w', encoding='utf-8') as file:
        file.write(content)

print("Done refactoring access patterns!")
