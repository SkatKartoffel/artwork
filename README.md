# Artwork – proofing for Packaged Sounds (Frappe / ERPNext app)

Turns customer-supplied artwork into print-ready, supplier-aligned PDFs with proper spot layers, the correct colour profile, ink limits and preflight warnings. Proofs go to the customer for approval, and everything stays linked to the Job Ticket (Project) and the SKU (Item).

## What it does

For every **Artwork Component** (one printed part = one SKU, e.g. `PSG1234.P` jacket or `.L` labels):

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
Job Ticket (Project) ──► Artwork Proof ──► Artwork Component (per SKU part) ──► processed in background
        ▲                    │  Request Artwork  → customer upload page (templates downloadable)
        │                    │  Send to Customer → email + combined proof PDF + approval page
        └── artwork_status ◄─┘  Approve / Request changes (customer, tokenised link)
                                     │
                     Item.approved_artwork ◄── print PDF of each component on approval
```

* **Project** gets an *Artwork Status* field, an *Artwork* button group and an Artwork section on its dashboard.
* **Item** gets *Default Supplier Template*, *Default Finish* (pre-fills new components) and, on approval, *Approved Print-Ready Artwork*, *Approved On Proof* and the approval date.
* Preflight **Errors must be ticked "OK'd"** (or fixed) before a proof can be sent. This can be switched off in settings.
* Approved proofs are locked. **New Revision** reopens them, and the revision number goes up each time a proof is sent.
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
   * `EuroscaleUncoated.icc` ships with Adobe Creative Cloud: on a Mac it's in `/Library/Application Support/Adobe/Color/Profiles/Recommended/`.
2. **Artwork Supplier Template.** One per supplier × product, e.g. *Takt – 12" Wide Spine 5mm*:
   * attach the supplier PDF and click **Detect Geometry**
   * check the trim size, bleed and spine width (set *Spine Centre Override* if detection misses)
   * use spine width 0 for labels and stickers
3. **Artwork Print Upgrade.** Six are seeded. Edit the aliases to match how your customers name spots.
4. **Artwork Settings.** Set the limits, template spot name, proof logo, header colour and email texts.
5. **Item.** Optionally set *Default Supplier Template* and *Default Finish* on SKU items, so components fill themselves in.
6. **Roles.** Give staff the **Artwork User** role; template, profile and settings admins need **Artwork Manager**.

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
artwork/processing.py  Frappe ↔ engine (background job, file handling)
artwork/api.py         desk helpers + guest portal endpoints
artwork/www/           artwork_upload, artwork_approval (customer pages)
artwork/tests/         fixtures + engine test
```

Engine test (no Frappe needed): `python -m artwork.tests.test_engine`.
