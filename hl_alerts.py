"""Alertes sur les positions BTC et or des comptes Hyperliquid les plus rentables (données publiques on-chain).

Aucun ordre n'est passé : le script ne fait que lire et signaler.

  python -I hl_alerts.py choisir     sélectionne les comptes et écrit hl_comptes.json
  python -I hl_alerts.py etat        affiche leurs positions BTC/or actuelles
  python -I hl_alerts.py surveiller  boucle : une ligne par position ouverte, renforcée, réduite ou fermée
  python -I hl_alerts.py passage     un seul tour pour la tâche Windows « hl-gros-comptes » : téléphone + hl_mouvements.log

Sélection (critères fixes) : compte >= 1 M$, gains cumulés >= 5 M$ et rendement cumulé > 0, en gain sur
le dernier mois, volume du mois < 300 x le compte (exclut teneurs de marché et robots haute fréquence).
BTC : les 15 premiers par gains cumulés. Or : parmi les 300 premiers, ceux qui ont une position or (10 max).
"""
import json, os, sys, time, urllib.request
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
COMPTES = os.path.join(HERE, "hl_comptes.json")
ETAT = os.path.join(HERE, "hl_etat_positions.json")
ETAT_AUTO = os.path.join(HERE, "hl_auto_etat.json")
NB_BTC, NB_OR, TOP_OR = 15, 10, 300
SEUIL = 0.05                                  # variation de taille ignorée en dessous de 5 %
MIN_VALEUR = 100_000                          # positions de moins de 100 000 $ ignorées (poussière)
MIN_VARIATION = 250_000                       # renfort/réduction signalé seulement s'il dépasse 250 000 $
PAUSE = 60


def info(payload):
    r = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(payload).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.load(f)


def wp(row, window):
    return next(v for k, v in row["windowPerformances"] if k == window)


def heure():
    return dt.datetime.now().strftime("%d/%m %Hh%M")


def positions(compte):
    out = {}
    marches = [("", ("BTC", "PAXG"))] + ([("xyz", ("xyz:GOLD",))] if compte["or"] else [])
    for dex, coins in marches:
        st = info({"type": "clearinghouseState", "user": compte["adresse"], **({"dex": dex} if dex else {})})
        for ap in st["assetPositions"]:
            p = ap["position"]
            if p["coin"] in coins and (p["coin"] == "BTC" or compte["or"]) and float(p["positionValue"]) >= MIN_VALEUR:
                out[p["coin"]] = {"qte": float(p["szi"]), "entree": float(p["entryPx"]), "levier": p["leverage"]["value"],
                                  "valeur": float(p["positionValue"]), "latent": float(p["unrealizedPnl"]),
                                  "liq": p.get("liquidationPx")}
    return out


def choisir():
    url = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
    rows = json.load(urllib.request.urlopen(url, timeout=60))["leaderboardRows"]
    sel = [r for r in rows
           if float(r["accountValue"]) >= 1e6 and float(wp(r, "allTime")["pnl"]) >= 5e6
           and float(wp(r, "allTime")["roi"]) > 0 and float(wp(r, "month")["pnl"]) > 0
           and float(wp(r, "month")["vlm"]) < 300 * float(r["accountValue"])]
    sel.sort(key=lambda r: -float(wp(r, "allTime")["pnl"]))

    def fiche(rang, r, avec_or):
        a = r["ethAddress"]
        return {"adresse": a, "nom": r.get("displayName") or f"{a[:6]}…{a[-4:]}", "rang": rang,
                "compte_M": round(float(r["accountValue"]) / 1e6, 1),
                "gains_cumules_M": round(float(wp(r, "allTime")["pnl"]) / 1e6, 1),
                "gain_mois_M": round(float(wp(r, "month")["pnl"]) / 1e6, 2), "or": avec_or}

    comptes = [fiche(k, r, False) for k, r in enumerate(sel[:NB_BTC], 1)]
    n_or = 0
    for k, r in enumerate(sel[:TOP_OR], 1):
        if n_or >= NB_OR:
            break
        f = fiche(k, r, True)
        if positions(f):
            ancien = next((c for c in comptes if c["adresse"] == f["adresse"]), None)
            if ancien:
                ancien["or"] = True
            else:
                comptes.append(f)
            n_or += 1
        time.sleep(0.15)
    with open(COMPTES, "w", encoding="utf-8") as fh:
        json.dump(comptes, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"eligibles": len(sel), "suivis": len(comptes), "dont_or": n_or, "comptes": comptes},
                     ensure_ascii=False, indent=1))


def charger():
    with open(COMPTES, encoding="utf-8") as f:
        return json.load(f)


def libelle(c):
    return f"{c['nom']} (HL #{c['rang']}, +{c['gains_cumules_M']} M$ cumulés)"


def etat():
    for c in charger():
        p = positions(c)
        txt = ", ".join(f"{k} {'LONG' if v['qte'] > 0 else 'SHORT'} {v['valeur'] / 1e6:,.2f} M$ à {v['entree']:,.1f} x{v['levier']}"
                        for k, v in p.items()) or "aucune"
        print(f"{libelle(c)} : {txt}")
        time.sleep(0.15)


def evenements(nom, avant, apres):
    for coin in sorted(set(avant) | set(apres)):
        a, b = avant.get(coin), apres.get(coin)
        actif = "OR" if coin != "BTC" else "BTC"
        if a and b and (a["qte"] > 0) != (b["qte"] > 0):     # retournement : fermeture puis ouverture
            yield from evenements(nom, {coin: a}, {})
            yield from evenements(nom, {}, {coin: b})
            continue
        ref = b or a
        sens = "LONG (achat)" if ref["qte"] > 0 else "SHORT (vente)"
        if a is None:
            liq = f", liquidation {float(b['liq']):,.0f}" if b["liq"] else ""
            yield f"🟢 {nom} OUVRE {actif} {sens} à {b['entree']:,.1f}, levier x{b['levier']}, {b['valeur'] / 1e6:,.2f} M${liq}"
        elif b is None:
            yield f"🔴 {nom} FERME {actif} {sens} (entrée {a['entree']:,.1f}, dernier latent {a['latent'] / 1e3:+,.0f} k$)"
        elif abs(b["valeur"] - a["valeur"]) < MIN_VARIATION:
            continue
        elif abs(b["qte"]) > abs(a["qte"]) * (1 + SEUIL):
            yield f"➕ {nom} RENFORCE {actif} {sens} : {a['valeur'] / 1e6:,.2f} → {b['valeur'] / 1e6:,.2f} M$, entrée moyenne {b['entree']:,.1f}"
        elif abs(b["qte"]) < abs(a["qte"]) * (1 - SEUIL):
            yield f"➖ {nom} RÉDUIT {actif} {sens} : {a['valeur'] / 1e6:,.2f} → {b['valeur'] / 1e6:,.2f} M$, latent {b['latent'] / 1e3:+,.0f} k$"


def lire_json(path, defaut):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defaut


def ecrire_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def tour(comptes, memo):
    """Relit chaque compte, met memo à jour. Renvoie les événements, le nombre de comptes illisibles et la dernière erreur."""
    evts, rates, erreur = [], 0, ""
    for c in comptes:
        try:
            now = positions(c)
        except Exception as e:
            rates, erreur = rates + 1, str(e)
            continue
        if c["adresse"] not in memo:
            memo[c["adresse"]] = now
            continue
        avant, garde = memo[c["adresse"]], {}
        for coin in set(avant) | set(now):
            a = {coin: avant[coin]} if coin in avant else {}
            b = {coin: now[coin]} if coin in now else {}
            lignes = list(evenements(libelle(c), a, b))
            evts += [f"{heure()} {ligne}" for ligne in lignes]
            # sans événement, on garde la référence : les petites variations s'additionnent jusqu'au seuil
            ref = b if (lignes or not a or not b) else a
            garde.update(ref)
        memo[c["adresse"]] = garde
        time.sleep(0.3)
    return evts, rates, erreur


def surveiller():
    comptes, memo = charger(), lire_json(ETAT, {})
    while True:
        evts, rates, erreur = tour(comptes, memo)
        for ligne in evts:
            print(ligne, flush=True)
        if rates == len(comptes):
            print(f"⚠️ ERREUR accès Hyperliquid : {erreur[:120]}", flush=True)
        ecrire_json(ETAT, memo)
        time.sleep(PAUSE)


def envoyer(titre, message, priorite=3):
    """Notification sur le téléphone via ntfy.sh ; le sujet vient du secret NTFY_TOPIC (jamais écrit dans le dépôt)."""
    topic = os.environ["NTFY_TOPIC"]
    r = urllib.request.Request("https://ntfy.sh/", method="POST", headers={"Content-Type": "application/json"},
                               data=json.dumps({"topic": topic, "title": titre, "message": message, "priority": priorite}).encode())
    urllib.request.urlopen(r, timeout=30).read()


def journal(msg):
    with open(os.path.join(HERE, "hl_mouvements.log"), "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def passage():
    """Un passage pour la tâche Windows : ouvertures et fermetures envoyées sur le téléphone, tout le reste au journal.
    L'état n'avance qu'après un envoi réussi, un mouvement n'est donc pas perdu si le réseau tombe."""
    comptes, memo, auto = charger(), lire_json(ETAT, {}), lire_json(ETAT_AUTO, {})
    evts, rates, erreur = tour(comptes, memo)
    if rates == len(comptes):
        journal(f"{heure()} ERREUR accès Hyperliquid : {erreur[:150]}")
        if not auto.get("erreur"):
            envoyer("Hyperliquid : surveillance en erreur", erreur[:150])
            auto["erreur"] = True
            ecrire_json(ETAT_AUTO, auto)
        return
    importants = [e for e in evts if " OUVRE " in e or " FERME " in e]
    if importants:
        envoyer("Hyperliquid : gros comptes", "\n".join(importants) +
                "\n\nPositions d'autres traders, pas un signal de tes règles. Ouvre/ferme = passe au-dessus/sous 100 k$.")
        if sys.stdout:  # vers le Monitor de Claude, qui relaie en notification
            for e in importants:
                print(e, flush=True)
    for e in evts:
        journal(e)
    ecrire_json(ETAT, memo)
    auto["erreur"] = False
    now = dt.datetime.now()
    if now.hour >= 9 and auto.get("vivant") != f"{now:%Y-%m-%d}":
        btc = [p for pos in memo.values() for k, p in pos.items() if k == "BTC"]
        vend = sum(p["valeur"] for p in btc if p["qte"] < 0) / 1e6
        ach = sum(p["valeur"] for p in btc if p["qte"] > 0) / 1e6
        envoyer("Hyperliquid : surveillance active", f"BTC : vendeurs {vend:,.0f} M$, acheteurs {ach:,.0f} M$", priorite=2)
        auto["vivant"] = f"{now:%Y-%m-%d}"
    ecrire_json(ETAT_AUTO, auto)


if __name__ == "__main__":
    if sys.stdout:  # None sous pythonw.exe (tâche planifiée sans fenêtre)
        sys.stdout.reconfigure(encoding="utf-8")
    {"choisir": choisir, "etat": etat, "surveiller": surveiller, "passage": passage}[sys.argv[1] if len(sys.argv) > 1 else "etat"]()
