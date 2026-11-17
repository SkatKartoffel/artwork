# Artwork – proofing for Packaged Sounds (Frappe / ERPNext app)

Turns customer-supplied artwork into print-ready, supplier-aligned PDFs with proper spot layers, the correct colour profile, ink limits and preflight warnings. Proofs go to the customer for approval, and everything stays linked to the Job Ticket (Project) and the SKU (Item).

## What it does

For every **print item** on a job (any Item in the **Print** item group, e.g. `PSG1234.P` jacket, `.L` labels), the app does the following:

1. **Template alignment.** Places the art on the chosen supplier's template page, so the **art's spine centre lands exactly on the template's spine centre**.
   * The template spine comes from the fold lines in the template PDF (auto-detected), or from the *Spine Centre Override* on the template record.
   * The artwork's spine comes from the customer's own dieline spot (if they built on a template), otherwise from the art TrimBox centre, otherwise from the page centre. A manual nudge (mm) is available per component.
   * Vertical alignment uses trim centres. Art is clipped to the template bleed box.
2. **Template layer.** The supplier template is recoloured into a single spot colour (default `Template`), **set to overprint**, on its own PDF layer, so the RIP can drop the plate and it never knocks out or prints with the art.
3. **Print upgrades** (Spot UV, Hot Foil Gold/Silver, Emboss, Deboss, White…). Each becomes its own **overprinting spot colour on its own layer**. Three ways to supply one:
   * **Artwork spot**: a named spot in the customer's file (`Spot UV`, `UV`, `Gold Foil`…, matched via the upgrade's aliases). It is lifted out of the art into its own layer.
   * **Separate file**: a black-only mask (PDF/AI/EPS/TIFF/PNG). Vectors are recoloured; raster masks become 1-bit stencils.
   * **Artwork page**: a page in the customer's PDF (e.g. page 2 = UV).
4. **Colour and ink.** The **finish** picks the profile:
   * Gloss / Matt → **ISO Coated v2 300% (ECI)**, ink capped at **300%**
   * Reverse Board / Uncoated → **Euroscale Uncoated v2**, ink capped at **260%**

   RGB, Lab and embedded-ICC colour (fills, images, gradients, palettes, patterns) is converted into the selected ICC profile with **LittleCMS** (relative colorimetric + black-point compensation by default). Ghostscript is only used to flatten spots and outline fonts, because its PDF writer ignores custom output profiles. CMYK that is already over the limit (vector fills, CMYK images, indexed palettes, simple gradients) is capped with GCR then CMY reduction, so pure K stays pure K. Pantones are converted to their CMYK alternates (optional: keep). The profile is embedded as the PDF OutputIntent. If anything can't be converted (rare gradient meshes), you get an `RGB_LEFT` warning.
5. **Fonts to outlines** (checkbox, default on). Small-text checks run *before* outlining.
6. **Preflight warnings:**
   * artwork less than **300dpi** (Error below 150dpi)
   * font size less than **5pt**
   * fonts not embedded
   * hairlines under 0.25pt
   * text crossing a spine fold, or inside the safe margin
   * insufficient bleed
   * art size ≠ template trim
   * spine width mismatch
   * unselected finish spots found in the art
   * upgrade selected but missing or empty
   * remaining ink over the limit

   All limits are set in **Artwork Settings**.
7. **Outputs** (attached to the component, private files):
   * `…_print.pdf`: print-ready. Layers: *Artwork (CMYK)* → upgrade spots → *Template*. TrimBox and BleedBox are set.
   * `…_preview.png`: screen preview, with finishes shown as coloured overlays.
   * `…_proof.pdf`: A3 customer proof sheet with job details, layer legend and preflight list.

## Workflow

```
Job Ticket (Project) ──► Artwork Proof ──► one row per print item (pulled from the Sales Order + BOMs)
        ▲                    │  drop artwork into a row → processed automatically in the background
        │                    │  Request Artwork  → customer upload page (templates downloadable)
        │                    │  Send to Customer → email + combined proof PDF + approval page
        └── artwork_status ◄─┘  Approve / Request changes (customer, tokenised link)
                                     │
                     Item.approved_artwork ◄── print-ready PDF of each row on approval
```

* **One screen.** Everything happens on the **Artwork Proof**. Creating one for a Job Ticket automatically adds every print item on its Sales Order, including print parts inside the default BOMs of the ordered records (sleeves and labels usually sit in the BOM). You can also use *Actions → Get Print Items from Sales Order*, or add rows by hand; the item picker only shows Print items.
* **Print spec lives on the Item.** Each print SKU has an *Artwork / Print Spec* section on the Item: Supplier Template, Finish, Convert Fonts to Outlines, and its Print Upgrades (Spot UV, foil… and how each is supplied). Set it once and represses reuse it automatically. A row whose Item has no spec shows **Needs Setup** with a message saying what's missing.
* **Finish masks.** If an upgrade is supplied as a *Separate file*, the proof adds a mask row for it automatically (staff or the customer upload the black-only file there).
* Dropping artwork into a row (or changing its page, nudge or mask) saves the proof and queues just that row. Each row shows its preview, status, warnings and files under the table.
* Preflight **Errors must be ticked "OK'd"** (or fixed) before a proof can be sent. This can be switched off in settings.
* Approved proofs are locked; **New Revision** reopens them, and the revision number goes up each time a proof is sent.
* **Job Ticket (Project)** gets an *Artwork Status* field and an Artwork section on its dashboard. On approval it also gets a timeline note, and each Item stores *Approved Print-Ready Artwork*, *Approved On Proof* and the approval date.
* Other apps (e.g. `milomrp`) can react to customer decisions via hooks:
  ```python
  artwork_on_approve = ["milomrp.artwork_hooks.on_approve"]            # fn(proof_doc)
  artwork_on_changes_requested = ["milomrp.artwork_hooks.on_changes"]
  ```

## Install (bench on the 20i VPS)

```bash
sudo apt install ghostscript            # required (pdfwrite + ICC conversion)
cd ~/frappe-bench
bench get-app https://github.com/<you>/artwork   # or copy this folder into apps/ and: bench pip install -e apps/artwork
bench --site erp.packagedsounds.com install-app artwork
bench --site erp.packagedsounds.com migrate
bench build --app artwork
bench restart
```

Python deps (`pikepdf`, `PyMuPDF`, `numpy`, `Pillow`) install automatically from `pyproject.toml`.

**Large artwork files:** raise the upload limit. Set `"max_file_size": 524288000` (500 MB) in `sites/erp.packagedsounds.com/site_config.json`, and `client_max_body_size 500m;` in the nginx site config.

**Background jobs:** processing runs on the `long` queue, so make sure a long worker is running (the default `bench setup supervisor` config includes one).

## First-time setup

1. **Artwork Colour Profile.** Attach the ICC files to the two seeded profiles:
   * `ISOcoated_v2_300_eci.icc` from eci.org (ECI offset profiles package).
   * `EuroscaleUncoated.icc` ships with Adobe Creative Cloud.
2. **Artwork Supplier Template.** One per supplier × product, e.g. *Takt – 12" Wide Spine 5mm*:
   * attach the supplier PDF and click **Detect Geometry**
   * check the trim size, bleed and spine width (set *Spine Centre Override* if detection misses)
   * use spine width 0 for labels and stickers
3. **Item Group "Print".** Created on install. Put your print SKUs in it, or in sub-groups such as Print › Labels. You can use a different group in Artwork Settings.
4. **Each print Item.** Fill in its *Artwork / Print Spec* section: template, finish and upgrades.
5. **Artwork Print Upgrade.** Six are seeded. Edit the aliases to match how your customers name spots.
6. **Artwork Settings.** Set the limits, template spot name, proof logo, header colour and email texts.
7. **Roles.** Give staff the **Artwork User** role; template, profile and settings admins need **Artwork Manager**.

## Code layout

```
artwork/engine/        framework-free PDF engine (testable without Frappe)
  pipeline.py          orchestrates one component
  walker.py            content-stream rewriter: spot / extract / remove / tac modes
  normalise.py         any input → upright single-page PDF
  geometry.py          spine + trim detection
  colour.py            Ghostscript conversion, ink limiting, OutputIntent
  preflight.py         warnings
  compose.py           final layered PDF (OCGs, overprint, boxes)
  preview.py           PNG preview + proof sheet
artwork/processing.py  Frappe ↔ engine (one background job per proof, file handling)
artwork/api.py         desk helpers + guest portal endpoints
artwork/www/           artwork_upload, artwork_approval (customer pages)
artwork/tests/         fixtures + engine test
```

Engine test (no Frappe needed): `python -m artwork.tests.test_engine`.
