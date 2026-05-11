import sqlite3
conn = sqlite3.connect(r'c:\CV- Toposheet\results\toposheet.db')
c = conn.cursor()
c.execute("UPDATE features SET survey_year='1934-35' WHERE map_name LIKE '%Ganjam%'")
conn.commit()
c.execute("SELECT survey_year, typeof(survey_year) FROM features WHERE map_name LIKE '%Ganjam%' LIMIT 1")
row = c.fetchone()
print(f'Value: {row[0]!r}  Type in DB: {row[1]}')
conn.close()
