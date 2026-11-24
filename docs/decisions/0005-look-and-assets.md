# 0005 Look of the admin app: own assets, readable first

Status: built.

## Decision

- The app should look friendly and a little playful, in the spirit of 1990s desktop programs. It is a small tool opened rarely, not a dashboard.
- **Only own or freely licensed assets.** No names, logos or recognisable marks of existing products, and no copies of existing artworks. Generated images may serve as a mood board; they are not shipped.
- The style lives in variables (colours, sizes, fonts) so it can be changed in one place.
- Panels over a busy background get a strong blur and a mostly opaque light fill, so text stays readable.
- **Readability of what the user is asked to approve (name, address, folder, note) comes before decoration.**

## Consequences

- The look can be swapped without touching function.
- Mascot, icons and sounds are drawn or recorded for this project.

## Exception: the hall of fame in the game Postwurf

The small game on the desktop shows a hall of fame. Its scores are invented. Its names are a wink at fictional characters from popular science fiction, as a homage. This is a conscious exception to the rule against names of existing products, taken and answered for by the project owner.

- Only names, in plain text. No pictures, logos, quotes or other material from the works.
- Only fictional characters, never real people.
- The list lives in one place (`frontend/src/logic/postwurf.ts`) and can be replaced by invented names at any time, for example if a rights holder objects.
- The names are not rights-cleared. This is a decision, not legal advice.
