"""Add Japanese translations to attrs_tags JSON option tables.

Converts English-only labels to 'English / 日本語' format so _lang_label
can split them at runtime. Only touches keys we have a translation for.
Idempotent: skips entries already containing CJK characters.
"""
import json, sys, os

# Translation dictionaries — English label → Japanese
# Keys in _TR are the *first token* of the English label (or full label).
_TR = {
    "E_Color": {
        "Closed / No eyes": "閉じている／目なし",
        "Brown": "茶",
        "Blue": "青",
        "Hazel / Green-Brown": "ヘーゼル",
        "Hazel": "ヘーゼル",
        "Amber / Golden Yellow": "琥珀",
        "Amber": "琥珀",
        "Gray": "灰",
        "Green": "緑",
        "Violet / Purple": "紫",
        "Violet": "紫",
        "Red / Pink": "赤／ピンク",
        "Red": "赤",
        "Silver / Metallic": "銀",
        "Silver": "銀",
        "Black": "黒",
        "Heterochromia (Complete)": "虹彩異色（完全）",
        "Heterochromia (Sectoral)": "虹彩異色（部分）",
        "Heterochromia (Central)": "虹彩異色（中心）",
        "Aniridia": "無虹彩症",
    },
    "E_Additional": {
        "Naked eye": "裸眼",
        "Glasses": "眼鏡",
        "Neon": "ネオン",
        "Sunglasses": "サングラス",
        "Goggles": "ゴーグル",
        "Eye patch": "眼帯",
        "Mask": "仮面",
        "Contact lens": "コンタクト",
    },
    "HC_Color": {
        "No hair": "髪なし",
        "Black": "黒",
        "Dark Brown": "黒髪",
        "Light Brown": "茶髪",
        "Blonde": "金髪",
        "Platinum Blonde": "プラチナブロンド",
        "Red": "赤",
        "Pink": "ピンク",
        "Ginger": "赤毛",
        "Gray": "灰",
        "White": "白",
        "Blue": "青",
        "Yellow": "黄",
        "Green": "緑",
        "Rainbow": "レインボー",
        "Neon": "ネオン",
    },
    "HC_Style": {
        "(none)": "（なし）",
        "Straight": "ストレート",
        "Wavy": "ウェーブ",
        "Curly": "カール",
        "Voluminous / Puffy": "ボリューム",
        "Voluminous": "ボリューム",
        "Bob": "ボブ",
        "Ponytail": "ポニーテール",
        "Braid": "三つ編み",
        "Tied": "結び髪",
        "Bun / Tied Up": "お団子",
        "Bun": "お団子",
        "Buzz": "坊主",
        "Buzz Cut / Shaved": "坊主刈り",
        "Buzz Cut": "坊主刈り",
    },
    "HC_Length": {
        "(none)": "（なし）",
        "Very Short / Nearly Shaved": "ごく短い",
        "Very Short": "ごく短い",
        "Short / Above Ears": "短い",
        "Short": "短い",
        "Medium / To Shoulders": "肩まで",
        "Medium": "中くらい",
        "Long / Below Shoulders": "長い",
        "Long": "長い",
        "Very Long / To Waist": "腰まで",
        "Very Long": "非常に長い",
        "Extremely Long": "超ロング",
    },
    "FA_Dir": {
        "Front": "正面",
        "Forward / Facing Camera": "カメラ向き",
        "Right": "右",
        "Right 3/4": "右斜め",
        "Right Profile": "右横顔",
        "Back": "後ろ",
        "Left 3/4": "左斜め",
        "Left": "左",
        "Left Profile": "左横顔",
    },
    "FA_Vert": {
        "Horizontal": "水平",
        "Upward": "上向き",
        "Downward": "下向き",
        "Tilted": "傾き",
    },
    "SK_Type": {
        "Type I — Very Fair": "タイプI — 非常に白い",
        "Type II — Fair": "タイプII — 白い",
        "Type III — Medium": "タイプIII — 普通",
        "Type IV — Olive": "タイプIV — オリーブ",
        "Type V — Brown": "タイプV — 褐色",
        "Type VI — Dark Brown": "タイプVI — 濃褐色",
        "Type VII — Black": "タイプVII — 黒",
    },
    "PM_Motion": {
        "Basic": "基本",
        "Still": "静止",
        "Walking": "歩く",
        "Running": "走る",
        "Jumping": "跳ぶ",
        "Dancing": "踊る",
        "Talking / Speaking": "話す",
        "Eating": "食べる",
        "Fighting / Combat": "戦う",
        "Sleeping": "眠る",
        "Swimming": "泳ぐ",
    },
    "PM_Posture": {
        "Standing": "立つ",
        "Sitting": "座る",
        "Lying": "横になる",
        "Kneeling": "膝立ち",
        "Leaning": "もたれる",
        "Bending": "屈む",
        "Crouching / Squatting": "しゃがむ",
        "Handstand / Upside Down": "逆立ち",
    },
    "CS_Light": {
        "Natural": "自然光",
        "Daylight": "昼光",
        "Soft": "ソフト",
        "Hard": "ハード",
        "Backlight": "逆光",
        "Rim light": "リムライト",
        "Low key": "ローキー",
        "High key": "ハイキー",
        "Neon": "ネオン",
    },
    "CS_Angle": {
        "Eye level": "目線",
        "Low angle": "ローアングル",
        "High angle": "ハイアングル",
        "Bird's eye": "俯瞰",
        "Worm's eye": "煽り",
        "Dutch": "ダッチ",
    },
    "CS_Shot": {
        "Extreme Close-Up": "超クローズアップ",
        "Face Close-Up": "フェイスアップ",
        "Big Close-Up": "ビッグクローズアップ",
        "Close-Up": "クローズアップ",
        "Bust Shot": "バストショット",
        "Medium Close-Up": "ミディアムクローズアップ",
        "Medium Shot": "ミディアムショット",
        "Cowboy Shot": "カウボーイショット",
        "Full Shot": "フルショット",
        "Wide Shot": "ワイドショット",
        "Extreme Wide Shot": "ロングショット",
    },
    "B_Shape": {
        "Natural": "自然",
        "Rounded": "丸型",
        "Pointed": "尖型",
        "Conical": "円錐",
        "Pear": "洋梨",
        "Teardrop": "しずく",
    },
    "B_Size": {
        "Flat / Male / Neutral": "フラット",
        "Athletic / Pectorals": "アスレチック",
        "Petite / AAA-A": "小さめ",
        "Small / B-C": "小",
        "Medium / D-E": "中",
        "Large / F-G": "大",
        "Extra Large / H+": "特大",
    },
    "WH_Hip": {
        "Slim": "細い",
        "Athletic / Firm": "引き締まった",
        "Average": "普通",
        "Curvy / Full": "豊満",
        "Large / Wide": "大きい",
    },
    "WH_Waist": {
        "Flat / Thin": "細い",
        "Athletic / Firm": "引き締まった",
        "Average": "普通",
        "Curvy / Full": "豊満",
        "Large / Wide": "大きい",
    },
    "Background_Table": {
        "Solid BG": "単色背景",
        "Black BG": "黒背景",
        "White BG": "白背景",
        "Green BG": "緑背景",
        "Indoor": "屋内",
        "Bedroom": "寝室",
        "Living Room": "リビング",
        "Outdoor": "屋外",
        "Outside of House": "家の外",
        "Swimming pool": "プール",
        "Commercial": "商業施設",
        "Store/Shop": "店舗",
        "Store / Shop": "店舗",
        "Restaurant/Cafe": "飲食店",
        "Restaurant / Cafe": "飲食店",
        "Office": "オフィス",
        "Hospital": "病院",
        "School": "学校",
        "Nature": "自然",
        "Beach": "浜辺",
        "Ocean": "海",
        "Lake": "湖",
        "Forest": "森",
        "Mountain": "山",
        "City": "街",
        "Street": "通り",
        "Park": "公園",
        "Space": "宇宙",
        "Stars": "星",
        "Moon Surface": "月面",
    },
    "Quality": {
        "good": "良",
        "bad": "不良",
        "ok": "普通",
        "best": "最高",
        "worst": "最低",
    },
    "Variant": {
        "original": "オリジナル",
        "upscale": "高解像度化",
        "crop": "トリミング",
        "edit": "編集",
        "remix": "リミックス",
    },
}

# Translations for text field labels and placeholders (__text_fields__)
_TEXT_FIELDS = {
    "prompt": {"label": "Positive Prompt / ポジティブプロンプト"},
    "neg_prompt": {"label": "Negative Prompt / ネガティブプロンプト"},
    "seed": {"label": "Seed / シード"},
    "note": {"label": "Note / ノート"},
    "speech": {"label": "Speech / Description", "_skip_conflict": True},  # has '/' but EN-only
    "project": {"label": "Title / タイトル"},
    "scene": {"label": "Scene / シーン"},
    "model": {"label": "Model / モデル"},
}


def has_japanese(s):
    return any('぀' <= c <= 'ゟ' or '゠' <= c <= 'ヿ' or '一' <= c <= '鿿' for c in s)


def translate_table(data, key):
    tr = _TR.get(key)
    if not tr:
        return 0
    table = data.get(key)
    if not isinstance(table, list):
        return 0
    changed = 0
    for row in table:
        if not (isinstance(row, list) and len(row) >= 2 and isinstance(row[1], str)):
            continue
        lbl = row[1]
        if has_japanese(lbl):
            continue
        ja = tr.get(lbl)
        if ja:
            row[1] = f"{lbl} / {ja}"
            changed += 1
    return changed


def translate_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    total = 0
    for key in list(data.keys()):
        total += translate_table(data, key)
    # Text fields: label/placeholder
    tf = data.get("__text_fields__", {})
    if isinstance(tf, dict):
        for k, meta in tf.items():
            if not isinstance(meta, dict):
                continue
            ref = _TEXT_FIELDS.get(k)
            if not ref:
                continue
            lbl = meta.get("label", "")
            if lbl and not has_japanese(lbl) and "label" in ref:
                new_lbl = ref["label"]
                if new_lbl != lbl and "/" in new_lbl:
                    meta["label"] = new_lbl
                    total += 1
    if total:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return total


if __name__ == "__main__":
    import glob
    for p in glob.glob("/mnt/1TBSSD/AIsearch/data/attrs_tags*.json"):
        n = translate_file(p)
        print(f"{os.path.basename(p)}: {n} labels translated")
