# S.A.S.S.O. · dati in quota

Due volte al giorno (13:07 e 21:07, ora legale) GitHub Actions legge in **sola lettura** il cloud EPEVER Solar Guardian dell'impianto *Sasso_PoliTo*. Poi pubblica tre file JSON, che la pagina del progetto legge direttamente:

| File | Contenuto |
|---|---|
| `data/latest.json` | Energia prodotta oggi, nel mese, nell'anno e in totale. Contiene anche la produzione ora per ora di oggi e, se disponibili, i valori dei 4 regolatori (potenza dei moduli, tensione e SOC della batteria, temperature). |
| `data/daily.json` | Produzione giornaliera degli ultimi due mesi circa |
| `data/monthly.json` | Produzione mensile dell'anno in corso e di quello precedente |
| `data/archive/AAAA-MM.json` | Archivio permanente: produzione ora per ora e una fotografia dei 4 regolatori a ogni lettura. Ogni lettura recupera anche il giorno precedente. |

## Messa in funzione (una volta sola)

1. **Crea un repository GitHub** (ad esempio `sasso-dati`) e carica questi file.
   - Il repository deve essere **pubblico** se vuoi usare GitHub Pages con un account gratuito. Le credenziali restano comunque private, perché sono salvate come secret.
2. **Aggiungi le credenziali come secret**:
   - vai su *Settings → Secrets and variables → Actions → New repository secret*;
   - crea `EPEVER_ACCOUNT` con l'email dell'account Solar Guardian;
   - crea `EPEVER_PASSWORD` con la password.

   Queste credenziali non vanno scritte in nessun file né inviate in chat.
3. **Attiva GitHub Pages**: *Settings → Pages → Deploy from a branch → `main` / root*.
   I dati saranno raggiungibili a `https://<utente>.github.io/<repository>/data/latest.json`.
4. **Fai la prima prova**: apri *Actions → "Dati fotovoltaico EPEVER" → Run workflow*. Se l'esecuzione è verde, nella cartella `data/` compaiono i tre JSON. Da quel momento l'aggiornamento parte da solo due volte al giorno.

## Come funziona e cosa può andare storto

- **Login e token.** Il login avviene una volta sola; il token (valido 30 giorni) resta nella cache di GitHub Actions e viene rinnovato ogni 7 giorni. Il login non viene ripetuto a ogni esecuzione.
- **Captcha.** Se Solar Guardian chiede il captcha (codice 1756), l'esecuzione si ferma con un errore e **non insiste**: GitHub ti avvisa via email. In quel caso entra una volta dal portale <https://hncloud.epsolarpv.com> e rilancia il workflow.
- **Sola lettura.** Lo script può chiamare soltanto i percorsi elencati in `READ_ONLY_PATHS`. Le chiamate che cambiano impostazioni o accendono e spengono i carichi sono escluse.
- **API non ufficiali.** Le chiamate sono quelle del portale web, non un'API pubblica, e un aggiornamento di EPEVER potrebbe romperle. Abbiamo chiesto a EPEVER un accesso ufficiale.
- **Valori dei regolatori: sperimentali.** Se il loro server non risponde, `latest.json` contiene comunque i dati di energia e riporta `controllers_error`.
- **Produzione di oggi.** È calcolata come differenza del contatore totale rispetto alla mezzanotte. Il contatore "oggi" del portale viene azzerato da ogni regolatore al risveglio del mattino, quindi nelle prime ore riporta ancora il valore del giorno prima.
