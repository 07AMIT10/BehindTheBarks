# Fallback footage sources

Every file the offline demo uses is listed here with its source and licence.
`python scripts/sources_stub.py` adds a TODO row for each new file in `raw/`;
`python scripts/sources_stub.py --check` lists what is still unlisted or TODO.

## Raw clips (`data/fallback/raw/`, not committed)

| File | Source URL | Licence | Shows |
|---|---|---|---|
| raw/german_shepherd_treat.webm | https://commons.wikimedia.org/wiki/File:German_shepherd_eating_dog_treat.webm | CC BY-SA 4.0, author: Shadster | German shepherd eating a treat (dev/test clip only, not in the fallback pack) |
| raw/malamute_snow.webm | https://commons.wikimedia.org/wiki/File:Ashley._Alaskan_Malamute_playing_in_the_snow_2015-03-01_West_Lafayette_IN-USA.webm | CC BY-SA 4.0, author: Ashleyam | Alaskan Malamute playing in snow (dev/test clip only, not in the fallback pack) |
| raw/pexels-12251902-stray-dog-resting-street.mp4 | https://www.pexels.com/video/12251902/ (inferred, verify) | Pexels License (free to use, no attribution required) | Stray dog resting with eyes closed on a street, head and front half only. Unused (no tail, blurred background) |
| raw/pexels-13372728-dog-face-behind-bars.mp4 | https://www.pexels.com/video/13372728/ (inferred, verify) | Pexels License (free to use, no attribution required) | Dog face close-up behind cage bars, mouth open. Unused (face only, bars occlude) |
| raw/pexels-16257945-chained-dog-barking-portrait.mp4 | https://www.pexels.com/video/16257945/ (inferred, verify) | Pexels License (free to use, no attribution required) | Tethered dog by a kennel pacing and barking, portrait, real audio. Used: vocal_chained |
| raw/pexels-17029736-poodle-treat-above-head-portrait.mp4 | https://www.pexels.com/video/17029736/ (inferred, verify) | Pexels License (free to use, no attribution required) | Poodle staring up at a raised treat, portrait. Used: treat_poodle |
| raw/pexels-4106998-yorkie-eating-bowl.mp4 | https://www.pexels.com/video/4106998/ (inferred, verify) | Pexels License (free to use, no attribution required) | Yorkie eating from a bowl, side view, silent audio track. Unused spare (second eating clip) |
| raw/pexels-5263066-corgi-waiting-by-bowl.mp4 | https://www.pexels.com/video/5263066/ (inferred, verify) | Pexels License (free to use, no attribution required) | Corgi sitting behind its bowl, mouth open. Used: waiting_corgi |
| raw/pexels-5359586-labrador-lying-by-bowl.mp4 | https://www.pexels.com/video/5359586/ (inferred, verify) | Pexels License (free to use, no attribution required) | Labrador lying beside a full bowl, ignoring it. Used: idle_labrador |
| raw/pexels-5728444-two-yorkies-studio-portrait.mp4 | https://www.pexels.com/video/5728444/ (inferred, verify) | Pexels License (free to use, no attribution required) | Two Yorkies on a studio backdrop, portrait, one leaves the frame. Unused (two dogs) |
| raw/pexels-7682694-chihuahua-lying-studio.mp4 | https://www.pexels.com/video/7682694/ (inferred, verify) | Pexels License (free to use, no attribution required) | Chihuahua lying calmly in a studio, looking around. Used: relaxed_chihuahua |
| raw/pexels-8056259-beagle-eating-owner-pouring.mp4 | https://www.pexels.com/video/8056259/ (inferred, verify) | Pexels License (free to use, no attribution required) | Beagle eating while its owner pours food and strokes it. Unused (person in frame) |
| raw/pexels-8056265-beagle-eating-topdown.mp4 | https://www.pexels.com/video/8056265/ (inferred, verify) | Pexels License (free to use, no attribution required) | Beagle eating, top-down, owner in frame. Unused (not a static bowl view) |
| raw/pexels-8434019-small-dog-eating-bowl-home.mp4 | https://www.pexels.com/video/8434019/ (inferred, verify) | Pexels License (free to use, no attribution required) | Small dog eating from a bowl at home, full body. Used: eating_home |
| raw/pexels-8730832-black-dog-beach-treat.mp4 | https://www.pexels.com/video/8730832/ (inferred, verify) | Pexels License (free to use, no attribution required) | Black dog on a beach taking treats from a hand; head and chest only. Used: treat_beach |
| raw/pexels-8730838-black-dog-beach-treat-closeup.mp4 | https://www.pexels.com/video/8730838/ (inferred, verify) | Pexels License (free to use, no attribution required) | Black dog on a beach taking a treat, head close-up. Unused (head only) |
| raw/pexels-8730841-collie-puppy-treat-closeup.mp4 | https://www.pexels.com/video/8730841/ (inferred, verify) | Pexels License (free to use, no attribution required) | Collie puppy sniffing a treat, head close-up. Unused (head only) |
| raw/pexels-9421537-shelter-dogs-behind-bars.mp4 | https://www.pexels.com/video/9421537/ (inferred, verify) | Pexels License (free to use, no attribution required) | Shelter dogs behind bars. Unused (several dogs, bars) |
| raw/pexels-9421538-shelter-dogs-barking-bars.mp4 | https://www.pexels.com/video/9421538/ (inferred, verify) | Pexels License (free to use, no attribution required) | Shelter dogs barking at bars. Unused (several dogs, bars) |

## Overlay audio (`data/fallback/overlays/`, not committed)

Sourced directly from Freesound rather than the ESC-50 crop, so the licence is per-clip (checked below),
not ESC-50's blanket CC BY-NC.

| File | Freesound id | Licence | Used in |
|---|---|---|---|
| overlays/170015__fabiopx__one-bark-of-a-poodle-dog.wav | 170015 (https://freesound.org/s/170015/) | CC0, author: fabiopx | treat_poodle |

## Derived clips (`data/fallback/clips/`)

Built with `scripts/mux_audio.py` (H.264/AAC, 30 fps, long side capped at 1280); the command is recorded so a clip can be rebuilt.
Only the original clips' own audio is used, except where an overlay is listed.

| Clip | Raw source | mux_audio.py arguments |
|---|---|---|
| clips/eating_home.mp4 | raw/pexels-8434019-small-dog-eating-bowl-home.mp4 | (none) |
| clips/treat_poodle.mp4 | raw/pexels-17029736-poodle-treat-above-head-portrait.mp4 | `--add overlays/170015__fabiopx__one-bark-of-a-poodle-dog.wav@3.0` |
| clips/treat_beach.mp4 | raw/pexels-8730832-black-dog-beach-treat.mp4 | (none) |
| clips/waiting_corgi.mp4 | raw/pexels-5263066-corgi-waiting-by-bowl.mp4 | (none) |
| clips/idle_labrador.mp4 | raw/pexels-5359586-labrador-lying-by-bowl.mp4 | (none) |
| clips/vocal_chained.mp4 | raw/pexels-16257945-chained-dog-barking-portrait.mp4 | `--orig-volume 1.0` (keeps the original barking audio) |
| clips/relaxed_chihuahua.mp4 | raw/pexels-7682694-chihuahua-lying-studio.mp4 | (none) |
