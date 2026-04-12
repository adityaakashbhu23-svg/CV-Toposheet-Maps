import sqlite3
conn = sqlite3.connect('results/toposheet.db')
cur = conn.cursor()
# Revert back to original file names
cur.execute("UPDATE features SET map_name='Map' WHERE map_name='Map of Part of Zillas Patna and Behar'")
cur.execute("UPDATE features SET map_name='Map1' WHERE map_name='72 P]16 Birbhum and Murshidabad Districts (1922)'")
cur.execute("UPDATE features SET map_name='Map2' WHERE map_name='Santhal Pargana, Maldah and Murshidabad (1970)'")
conn.commit()
for name in ['Map', 'Map1', 'Map2']:
    cur.execute("SELECT COUNT(*) FROM features WHERE map_name = ?", (name,))
    print(f"{name}: {cur.fetchone()[0]} rows")
conn.close()
print("Done.")
print("Done.")
