# AMS Matching Analysis: "Bambu PLA Basic"

## Problem
Tray text: `"Bambu PLA Basic"`
Vendor in Spoolman: `"Bambu Lab"`
Material in Spoolman: `"PLA"`

**Current matching logic (line 1111):**
```jinja2
{% if vendor and vendor in tray_lower %}{% set match_score = match_score + 1 %}{% endif %}
```

**Check:**
- `"bambu lab" in "bambu pla basic"` → **FALSE**
- Reason: "Bambu Lab" as a complete substring is NOT in "Bambu PLA Basic"

**Result:** match_score = 1 (only material "pla" matched) → No match (needs >=2)

---

## Root Cause
The substring check is too strict. Vendor names often have multiple words, but AMS tray text may only include the first word.

**Examples:**
- Vendor: `"Bambu Lab"` → Tray: `"Bambu PLA Basic"` → Mismatch
- Vendor: `"Overture"` → Tray: `"Overture PLA"` → Match ✓
- Vendor: `"Hatchbox"` → Tray: `"Hatchbox PLA+"` → Match ✓

**Single-word vendors work, multi-word vendors don't.**

---

## Solutions

### Option 1: Word-Based Vendor Matching (Recommended)
Check if ANY significant word from vendor appears in tray text (ignore common words like "Lab", "3D", "Printing", "Filament").

```jinja2
{% set vendor_words = vendor | lower | replace('lab', '') | replace('3d', '') | replace('printing', '') | replace('filament', '') | split() | select | list %}
{% set vendor_matched = false %}
{% for word in vendor_words %}
  {% if word and word | length > 2 and word in tray_lower %}
    {% set vendor_matched = true %}
  {% endif %}
{% endfor %}
{% if vendor_matched %}{% set match_score = match_score + 1 %}{% endif %}
```

**Result for "Bambu PLA Basic":**
- Vendor words: `["bambu"]` (after removing "lab")
- Check: `"bambu" in "bambu pla basic"` → **TRUE** ✓
- Match score: 2 (vendor + material) → **Match** ✓

---

### Option 2: Relaxed Substring (Simpler, Less Accurate)
Check if vendor OR first word of vendor is in tray text.

```jinja2
{% set vendor_first_word = vendor | lower | split() | first %}
{% if (vendor and vendor in tray_lower) or (vendor_first_word and vendor_first_word | length > 2 and vendor_first_word in tray_lower) %}
  {% set match_score = match_score + 1 %}
{% endif %}
```

**Result:**
- First word: `"bambu"`
- Check: `"bambu" in "bambu pla basic"` → **TRUE** ✓
- Match score: 2 → **Match** ✓

---

### Option 3: Vendor Aliases (Most Accurate, Most Work)
Maintain a mapping of vendor variations.

```jinja2
{% set vendor_aliases = {
  'bambu lab': ['bambu'],
  'hatchbox': ['hatchbox'],
  'overture': ['overture'],
  'esun': ['esun', 'e-sun']
} %}
```

**Not recommended:** Too much maintenance overhead.

---

## Recommendation

**Implement Option 2** (relaxed substring with first word fallback):
- Simplest fix
- Handles multi-word vendors
- Still requires 2/3 match (vendor + material or vendor + name)
- Minimal risk of false positives

**Test cases after fix:**
| Tray Text | Vendor | Material | Name | Match Score | Result |
|-----------|--------|----------|------|-------------|--------|
| "Bambu PLA Basic" | "Bambu Lab" | "PLA" | "Red" | 2 | **Match** ✓ (now) |
| "Overture PLA" | "Overture" | "PLA" | "White" | 2 | **Match** ✓ |
| "eSUN ABS" | "eSUN" | "ABS" | "Black" | 2 | **Match** ✓ |
| "Generic PLA" | "Bambu Lab" | "PLA" | "Red" | 1 | **No match** (correct) |

---

## Implementation

Update lines 1111-1113 in `automations.yaml`:

```yaml
# OLD:
{% if vendor and vendor in tray_lower %}{% set match_score = match_score + 1 %}{% endif %}

# NEW:
{% set vendor_first_word = vendor | lower | split() | first %}
{% if (vendor and vendor in tray_lower) or (vendor_first_word and vendor_first_word | length > 2 and vendor_first_word in tray_lower) %}
  {% set match_score = match_score + 1 %}
{% endif %}
```

Apply similar logic to material and name if needed (but these are usually single words already).
