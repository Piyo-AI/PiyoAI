# Set up parcel tracking

Piyo looks up a tracking number on the carrier's public tracking page and tells you the status. It never signs
in and never changes a delivery.

## One thing to set up

This skill uses Piyo's own browser. If you have not used it before, it needs a one-time download of about 150 MB:
follow the steps in **Browse the web** (Settings > Skills > Browse the web > Setup), or just ask for a parcel and
press **Install browser** when the banner appears.

## Try it

1. Say: "Where is my parcel? UPS 1Z999AA10123456784" (use a real number of yours).
2. Piyo opens the carrier's page and reports the status, the last scan, the expected delivery date and the page
   address. If the number format does not show the carrier, it asks you.

## Things worth knowing

- The tracking number is typed only into the carrier's own site (or a tracking aggregator for carriers it does
  not list).
- Carriers change their pages. If a lookup fails, open the address Piyo gives you yourself.
- If **Settings > Browser** has an allowed-sites list, add the carrier's site to it.

## What this skill can do

- Open and read public pages, wait for them to load, click and type on the tracking page.
- It needs no accounts or keys.
