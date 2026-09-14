import json
from collections import Counter, defaultdict
import re

with open('output/j1939_spn_fmi_database.json', 'r', encoding='utf-8') as f:
    db = json.load(f)

print(f"Total SPN entries: {len(db)}")

# 1. Inspect fields across all entries
all_keys = set()
for spn, val in db.items():
    all_keys.update(val.keys())
print("All keys across SPNs:", sorted(list(all_keys)))

# Count presence of fields
field_counts = Counter()
for spn, val in db.items():
    for k in val:
        if val[k]:
            field_counts[k] += 1
print("Field presence counts:")
for k, v in field_counts.most_common():
    print(f"  {k}: {v} ({v/len(db)*100:.1f}%)")
