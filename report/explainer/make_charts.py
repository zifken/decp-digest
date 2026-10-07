import pandas as pd, numpy as np
from plotnine import *
from pathlib import Path

OUT = Path('/home/kz/src/decp-digest/report/explainer'); OUT.mkdir(parents=True, exist_ok=True)
df = pd.read_parquet('/home/kz/src/decp-digest/data/consol/expiries.parquet',
                     columns=['date_notification','duree_mois','date_expiration','cpv','ac','montant'])
TODAY = pd.Timestamp('2026-09-28')
theme_k = theme_minimal(base_size=11) + theme(figure_size=(10, 5), plot_title=element_text(weight='bold'))

# 1. Concept: how an expiry forecast works (schematic, dates illustrative from one real row type: 48-month AC)
notif = pd.Timestamp('2023-06-15'); exp = notif + pd.DateOffset(months=48)
segs = pd.DataFrame({
    'y': ['1. Marché en cours', '2. Nouvel appel d\'offres publié', '3. Fenêtre de vente (notre alerte)'],
    'start': [notif, exp - pd.DateOffset(months=6), exp - pd.DateOffset(months=12)],
    'end': [exp, exp - pd.DateOffset(months=2), exp - pd.DateOffset(months=6)],
    'col': ['contrat', 'ao', 'alerte']})
segs['y'] = pd.Categorical(segs.y, categories=segs.y[::-1])
marks = pd.DataFrame({'x': [notif, exp, TODAY], 'lab': ['Notification\n(publiée dans DECP)', 'Échéance estimée\n= notif. + durée', "Aujourd'hui "], 'ha': ['center', 'left', 'right']})
p1 = (ggplot(segs) + geom_segment(aes(x='start', xend='end', y='y', yend='y', color='col'), size=9)
      + geom_vline(marks, aes(xintercept='x'), linetype='dashed', color='#555555')
      + geom_text(marks, aes(x='x', label='lab', ha='ha'), y=3.45, size=8, va='bottom')
      + scale_x_datetime(limits=(notif - pd.DateOffset(months=6), exp + pd.DateOffset(months=8)))
      + scale_color_manual(values={'contrat': '#9aa5b1', 'ao': '#d9822b', 'alerte': '#2b6cb0'}, guide=None)
      + scale_y_discrete(expand=(0, 0.6, 0, 1.0))
      + labs(title="L'idée : repérer les marchés qui vont être remis en concurrence",
             subtitle="Exemple type : accord-cadre de 48 mois.\nL'alerte arrive 6-12 mois avant l'échéance, avant la publication du nouvel AO.",
             x='', y='') + theme_k)
p1.save(OUT / '1_concept.png', dpi=130, verbose=False)

# 2. Real forward pipeline: expiries per month, next 24 months, by notification year
f = df[(df.date_expiration >= TODAY) & (df.date_expiration < TODAY + pd.DateOffset(months=24))].copy()
f['mois'] = f.date_expiration.dt.to_period('M').dt.to_timestamp()
f['annee_notif'] = f.date_notification.dt.year.clip(upper=2026).astype(str).where(f.date_notification.dt.year >= 2022, '≤2021')
g = f.groupby(['mois', 'annee_notif']).size().reset_index(name='n')
p2 = (ggplot(g, aes('mois', 'n', fill='annee_notif')) + geom_col()
      + annotate('rect', xmin=TODAY + pd.DateOffset(months=6), xmax=TODAY + pd.DateOffset(months=12), ymin=0, ymax=np.inf, alpha=0.08, fill='#2b6cb0')
      + annotate('text', x=TODAY + pd.DateOffset(months=9), y=g.groupby('mois').n.sum().max() * 1.02, label='fenêtre produit 6-12 mois', size=9, color='#2b6cb0')
      + scale_fill_brewer(type='qual', palette='Set2', name='Année de notification')
      + labs(title='Données réelles : marchés publics arrivant à échéance, 24 prochains mois (tous secteurs)',
             subtitle=f'{len(f):,} marchés, DECP consolidé, build du 2026-09-28.\nLes marchés notifiés en 2023 sont sous-représentés (voir graphique 3).'.replace(',', ' '),
             x="Mois d'échéance estimée", y='Nombre de marchés') + theme_k)
p2.save(OUT / '2_pipeline_reel.png', dpi=130, verbose=False)

# 3. Coverage: notifications per year, build v0 (before decp-2019.json) vs v1 (after)
BEFORE = {2019: 1167, 2020: 4060, 2021: 12751, 2022: 24725, 2023: 56766,
          2024: 213259, 2025: 218012, 2026: 112301}
import json
after = json.loads(Path('/home/kz/src/decp-digest/data/consol/build_stats.json').read_text())['volume_per_notification_year']
c = pd.DataFrame({'annee': sorted(BEFORE)})
c['avant'] = c.annee.map(BEFORE)
c['apres'] = c.annee.map(lambda y: int(after.get(str(y), 0)))
cl = c.melt(id_vars='annee', value_vars=['avant', 'apres'], var_name='build', value_name='n')
p3 = (ggplot(cl, aes('annee', 'n', fill='build')) + geom_col(stat='identity', position='dodge')
      + scale_fill_manual(values={'avant': '#c53030', 'apres': '#2b6cb0'}, name='', labels=['avant (sans decp-2019.json)', 'après ingestion'])
      + labs(title='Couverture : marchés notifiés par année, avant / après ingestion de decp-2019.json',
             subtitle="L'ingestion de decp-2019.json (2019-2023) multiplie la couverture 2019-2022 par ~8-10 et 2023 par 2,6.\n2023 reste sous 2024 : une partie du saut 2024 est réelle (réforme DECP / renforcement de l'obligation de publication).",
             x='Année de notification', y='Nombre de marchés') + theme_k + theme(legend_position='top'))
p3.save(OUT / '3_trou_couverture.png', dpi=130, verbose=False)
print(len(f)); print(c.to_string())
