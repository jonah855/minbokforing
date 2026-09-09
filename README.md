# Min Bokföring v13

V13 är byggd som en striktare och säkrare vidareutveckling av v12.

## Nytt

- Inställningar för K1/förenklat årsbokslut eller årsbokslutsspår.
- Momsperiod och bokföringsmetod som inställningar.
- Bokförda verifikationer är låsta i UI.
- Rättelser görs genom en ny omvänd korrigeringsverifikation.
- Hashkedja mellan verifikationer för att upptäcka efterhandsändringar.
- Auditlogg.
- Automatisk lokal backup av bokföringsdatabas + kvitton.
- Bankimport och deduplicering.
- Kvittoarkiv/OCR.
- Momsöversikt.
- Rapporter/PDF.
- Bokslutskontroll.
- NE-granskningsunderlag och SRU-underlag.

## Viktigt om laglighet

Programmet är inte certifierat och ska inte beskrivas som en garanti för korrekt bokföring.
V13 är byggd efter aktuell struktur från BFN/BAS/Skatteverket, men faktisk användning kräver att företagets verkliga förhållanden, moms, periodiseringar, tillgångar, skulder och NE-uppgifter kontrolleras.
SRU-filerna är uttryckligen tekniska underlag och inte godkända för inlämning utan kontroll mot aktuell Skatteverket-specifikation.

## K1

BFN anger att enskilda näringsidkare med nettoomsättning normalt högst 3 mkr kan välja förenklat årsbokslut. BAS har en särskild K1-kontoplan.
V13 väljer därför inte automatiskt K1 åt dig.

## OCR

På macOS använder appen i första hand Apples inbyggda Vision-ramverk för lokal OCR av HEIC, JPG och PNG. Hjälpprogrammet byggs första gången ett kvitto läses och kräver Apples Command Line Tools.

Om Vision inte kan användas faller appen tillbaka till Tesseract, som kan installeras med:

```sh
brew install tesseract tesseract-lang
```

OCR-förloppet skrivs till Terminalen så att problem kan felsökas utan att kvittots innehåll loggas.
