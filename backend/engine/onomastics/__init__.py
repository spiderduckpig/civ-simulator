import random
import os
import json
from pathlib import Path

def load_onomastics():
    """Loads all onomastics JSON files and returns a list of dictionaries."""
    onom_dir = Path(__file__).parent
    onom_list = []
    for file in onom_dir.glob("*.json"):
        with open(file, "r", encoding="utf-8") as f:
            onom_list.append(json.load(f))
    return onom_list if onom_list else [{
        "PRE": ["Ar"], "MID_S": ["an"], "SUF_S": ["ia"],
        "CPRE": [""], "CSUF": ["ton"], "LF": ["Arak"], "LL": ["the Bold"]
    }]

def gen_civ_name(onom: dict) -> str:
    """Generated using PRE + [MID_S] + SUF_S."""
    pre = random.choice(onom["PRE"])
    mid = ""
    if random.random() < 0.65:
        mid = random.choice(onom["MID_S"])
    suf = random.choice(onom["SUF_S"])
    return pre + mid + suf

def gen_city_name(onom: dict) -> str:
    """Generated using CPRE + (PRE + CSUF).title()."""
    cpre = random.choice(onom["CPRE"])
    pre = random.choice(onom["PRE"])
    csuf = random.choice(onom["CSUF"])
    return cpre + (pre + csuf).title()

def gen_leader_name(onom: dict) -> str:
    """Generated using LF + " " + LL."""
    lf = random.choice(onom["LF"])
    ll = random.choice(onom["LL"])
    return f"{lf} {ll}"

def gen_commander_name(onom: dict) -> str:
    """Usually same as leader name or simplified."""
    return gen_leader_name(onom)
