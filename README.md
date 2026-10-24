# PlexiPlay Media Check

Utilitaire Windows pour comprendre pourquoi une vidéo (par exemple un preroll) ne passe pas sur une TV.

1. **Examiner** : choisissez un fichier, ses caractéristiques précises s'affichent (conteneur, codec, profil, niveau, résolution, cadence, débit, images B, images clés, couleurs, audio…).
2. **Comparer** : choisissez un fichier qui fonctionne ; les différences sont surlignées en jaune.
3. **Réencoder** : crée une copie du premier fichier (`..._conforme`) avec les caractéristiques du second. L'original n'est pas modifié.

FFmpeg et ffprobe sont inclus dans l'exe : rien d'autre à installer.

## Limites
- Seuls H.264 et H.265 sont gérés comme modèle. « À l'identique » signifie mêmes paramètres, pas le même fichier octet pour octet (un réencodage reste un réencodage).
- Les métadonnées HDR et Dolby Vision ne sont pas reproduites.

## Essais en ligne de commande
```
python compare_tool.py --analyse fichier.mp4 [modele.mp4]
python compare_tool.py --commande fichier.mp4 modele.mp4
```

Projet personnel de Jean-Francois Guilard, non affilié à Plex.
