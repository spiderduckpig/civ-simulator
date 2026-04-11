import re

def main():
    with open('engine/civ.py', 'r', encoding='utf-8') as f:
        text = f.read()

    # 1. Update imports
    if 'from .models import Civ, City' not in text:
        text = text.replace('from .constants import (', 'from .models import Civ, City\nfrom .constants import (')
    
    # 2. Update make_civ return
    start_idx = text.find('    return {\n')
    if start_idx != -1:
        # find the end bracket
        end_idx = text.find('\n    }', start_idx)
        if end_idx != -1:
            replacement = """    return Civ(
        id=cid,
        name=gen_civ_name(onom),
        leader=gen_leader_name(onom),
        onom=onom,
        color=_next_color(),
        capital=spot,
        territory=territory,
        cities=[City(
            cell=spot,
            name=city_name,
            population=80.0,
            is_capital=True,
            founded=tick,
            trade=10.0,
            wealth=20.0,
            focus=random.choice([FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE]),
            near_river=cell_on_river(spot, rivers),
            coastal=cell_coastal(spot, ter),
            food_production=0.0,
            carrying_cap=200,
            tiles=[],
            farm_tiles=[],
            hp=115.0,
            max_hp=115.0,
            last_dmg_tick=-999,
        )],
        population=100.0,
        military=20.0,
        gold=50.0,
        food=80.0,
        tech=1.0,
        culture=1.0,
        age=0,
        alive=True,
        integrity=0.6 + random.random() * 0.35,
        aggressiveness=0.2 + random.random() * 0.7,
        relations={},
        allies=set(),
        power=0.0,
    )"""
            text = text[:start_idx] + replacement + text[end_idx+6:]
    
    with open('engine/civ.py', 'w', encoding='utf-8') as f:
        f.write(text)

    # 3. Update make_civ return type hint Optional[dict] -> Optional[Civ]
    with open('engine/civ.py', 'r', encoding='utf-8') as f:
        text = f.read()
    text = text.replace(') -> Optional[dict]:', ') -> Optional[Civ]:')
    with open('engine/civ.py', 'w', encoding='utf-8') as f:
        f.write(text)

    # ---------- main.py ----------
    with open('main.py', 'r', encoding='utf-8') as f:
        text = f.read()

    # The civ state is returned to the frontend via web sockets. The frontend still expects dictionaries.
    # Replace the serialization of cities.
    # In main.py `_ser_civs`
    
    text = text.replace('ci["cell"]', 'ci.cell')
    text = text.replace('ci["name"]', 'ci.name')
    text = text.replace('ci["population"]', 'ci.population')
    text = text.replace('ci["is_capital"]', 'ci.is_capital')
    text = text.replace('ci["hp"]', 'ci.hp')
    text = text.replace('ci["max_hp"]', 'ci.max_hp')
    
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(text)

if __name__ == '__main__':
    main()

