# Submission wording and recording checklist

## Links to include

- Prototype: https://public-demo-one.vercel.app/
- Technical documentation: https://public-demo-one.vercel.app/technical-documentation
- GitHub: https://github.com/hackthejack08-cmyk/oil-spills/tree/main/oilspill-intel

Use the documentation URL as a hyperlink labelled **Technical documentation** in the PPT/PDF. It is also linked inside the website. Test each link signed out before submitting. The YouTube field should contain the video URL, not the GitHub URL.

## Idea title

OSI: Satellite Oil-Spill Screening, Drift Analysis and AIS Investigation Support

## Opening hook

A suspected slick appears offshore. Where was it observed, where might it move, and which vessels should an investigator examine first? OSI brings the image, drift estimates and historical vessel evidence into one review workflow.

## Idea description

OSI is an oil-spill investigation prototype for Coast Guard, port and marine pollution-response teams. It connects georeferenced Sentinel-1 SAR screening, possible release-window reconstruction, forward drift estimates and historical AIS correlation. Its purpose is to help an analyst connect the evidence, understand what is uncertain and hand the case to the next officer.

The implemented baseline identifies candidate dark regions and applies geometry, contrast and look-alike checks. The analysis backend can retrieve environmental data for the scene's location and time, evaluate multiple release-age hypotheses and rank vessel leads using spatial, temporal and trajectory evidence. Missing or inferred AIS positions are exposed rather than treated as confirmed observations. Sentinel-2 and NASA GIBS imagery provide optical context; automated SAR–EO oil confirmation is not yet implemented.

A new Response Brief converts the selected result into a plain-language handoff. It contains observation time and location, suspected area, available movement and release estimates, the highest-ranked vessel lead and checks needed before action. The brief can be copied or downloaded. It flags synthetic data, missing results, expired forecast periods and relevant AIS gaps. It does not dispatch an alert or establish that a vessel caused pollution.

The public website provides live satellite catalogue previews, a saved synthetic end-to-end demonstration, evidence JSON and a labelled real-SAR sample pack. Arbitrary-image processing requires the separate Python backend; the public replay must not be described as live inference. In the packaged 19-scene baseline test, recall was 80.0% and negative false-alarm rate was 44.4% at the stated threshold. These small-sample results identify a need for stronger look-alike discrimination. Forecast and attribution accuracy have not yet been independently validated.

The next development steps are a larger incident-separated detector evaluation, forecast comparison against later observations, authorised regional AIS integration and an always-on secured monitoring deployment.

## Abstract / summary

OSI connects satellite oil-spill screening, drift hypotheses and historical vessel evidence in one analyst workflow. A geospatial backend processes SAR imagery, tests possible release windows and ranks investigative vessel leads. A new Response Brief provides observation details, available estimates and missing-evidence checks in a copyable, downloadable handoff. The public prototype demonstrates the workflow with labelled synthetic results, live catalogue previews and real-SAR test examples. It is decision support, not proof of pollution or vessel responsibility; arbitrary-image inference requires the Python backend, and forecast accuracy remains to be validated.

## Feature text for the PPT

Response Brief — converts the selected spill candidate, observation time, drift estimates and vessel leads into a readable officer handoff. Copy or download the brief with missing-data warnings and source references. No unsupported responsibility or coastal-arrival claim is made.

## Morning test and recording order

1. Open the public prototype and documentation links in a signed-out browser.
2. Run **Scan for oil spills**; show the **Demo data** label. Do not call this a live satellite inference run.
3. Wait for completion. Show the one candidate passing the public filter, the 14.20 km² area and the 75/100 evidence-match lead.
4. On **Home**, show **Response brief**, expand **Checks before handoff**, and download the text file. Explain the synthetic label and the 39-minute AIS gap.
5. Open **Evidence** and download the JSON report. Say hashes help check record integrity, not model accuracy.
6. Open **Accuracy** and show the recall and false-alarm counts. Download the real-SAR test pack; its ten examples are a subset, not the entire benchmark.
7. Show the live catalogue's image date/source separately. If the catalogue fails, say it is unavailable and identify the historical fallback. Do not imply a new image has been analysed if it is only a preview.
8. End with the prototype, documentation and GitHub links. Add the YouTube link after your recording is uploaded and check it signed out.

## Short recording narration

“A suspected slick is only the start of an investigation. An officer needs to know when it was observed, where it might have come from and which vessel tracks deserve review.

This is OSI. I am running our labelled synthetic scenario to demonstrate the complete workflow. The image, drift model and vessel results in this scenario are not a live pollution incident.

The system brings the candidate area, possible release window and vessel evidence together. This vessel ranks first on evidence match; the score is not a probability of guilt. Here we can see a gap in its AIS record.

Our Response Brief turns those details into a handoff the officer can copy or download, including what still needs checking. The evidence export retains the underlying record separately.

We also provide measured SAR examples and report the baseline's mistakes. Our small test found eight of ten positives, but four of nine look-alikes also triggered an alert. Reducing those false alarms and validating drift against later observations are the next priorities.

The technical documentation explains exactly what runs on the public site and what requires our backend. Our goal is to support an investigator's decision, not replace it.”

## Wording to avoid

- “Real-time oil detection every minute” — catalogue polling is not a new satellite observation or continuous inference.
- “Works on every internet image” — SAR analysis needs suitable pixels and reliable metadata.
- “75% accuracy” — the vessel ranking is a rule-based evidence score.
- “AI upscaling creates real detail” — display enhancement does not create measured SAR information.
- “Optical validation implemented” or “trained U-Net deployed” — neither is the current public implementation.
- “Confirmed culprit”, guaranteed response time or a measured forecast accuracy — unsupported.

No specific ATS or automatic keyword-scoring system has been verified for this problem statement. Use accurate, readable problem terms instead of keyword stuffing, and follow the current SIH portal's instructions.
