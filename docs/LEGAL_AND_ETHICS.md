# Legal & ethical use

*This page is practical guidance, not legal advice. Laws differ by country and
case — confirm with your supervisor, institution or counsel before you start.*

## Only examine data you are authorised to examine

Use WhatsApp Forensicator only on:

- **your own device and accounts**, or
- a device you hold under **written authorisation** — the owner's informed
  consent, a workplace/institutional policy that covers it, a court order or
  warrant, or an engagement letter from your client/agency.

Keep a copy of that authorisation with the case file. If you are unsure whether
you are allowed to image a phone, stop and ask first. Accessing someone else's
messages without authority can be a criminal offence (for example under India's
IT Act 2000 §43/§66, the US CFAA and Wiretap/Stored Communications Acts, the UK
Computer Misuse Act, and similar laws elsewhere) and can breach privacy and
data-protection law (GDPR, India's DPDP Act 2023, etc.).

## What this tool deliberately does not do

- No live call interception, VoIP sniffing or real-time location tracking.
- No lock-screen bypass, exploit or privilege-escalation code. It uses `adb`
  only on a device that is already unlocked and USB-debugging-authorised, and
  uses root only if the device is already rooted.
- No cloud-account access: it never logs in to Google Drive/iCloud.
- Fully offline — nothing is uploaded anywhere.

Please keep it that way if you extend it.

## Handling evidence properly

1. **Work from a case folder** (Case tab): originals land in `evidence/`, are
   hashed and set read-only; analysis runs on copies in `working/`.
2. **Record who did what**: enter your name and the case ID before extracting.
   Every pull, decrypt and export is SHA-256 hashed and written to a
   hash-chained custody log. Run *Verify integrity* before you report.
3. **Document the device**: model, serial, Android version, time zone, and the
   exact method used (root / backup / on-device export).
4. **Minimise**: examine and export only what the authorisation covers. Treat
   third parties' messages, photos and contacts as sensitive personal data.
5. **Protect the output**: store case folders on encrypted, access-controlled
   media; delete working copies when the case ends and retention rules allow.
6. **Be honest about limits**: face matching, OCR and parser results are
   investigative leads. Validate against a known sample, and say in your report
   which tool version and settings produced each result.
7. **Court use**: courts often need a certificate or expert statement that
   explains how electronic records were obtained and that they are unaltered
   (e.g. India's Bharatiya Sakshya Adhiniyam 2023 §63, formerly Evidence Act
   §65B). The custody log and PDF report help, but they are not a substitute
   for that process. Read-only flags in this tool are not a hardware write
   blocker.

## Face matching

Person search compares faces against a reference photo you supply. Only use it
when your authorisation covers identifying that person, never treat a match as
an identification, and review every hit manually. Face recognition is less
reliable across pose, lighting, age and skin tone, so false matches and misses
are expected.
