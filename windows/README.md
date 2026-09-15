# Hunter on your PC

Hunter does everything in the cloud except the last click. This is that last
click.

## Why anything has to run here at all

A web page is not allowed to attach a file to a form. That is a browser security
rule, not something any site or any link can change, and it is why an email
button can never hand you a form with your CV already on it. A browser being
driven by a program *can* do it, and that program has to be on the machine with
the browser.

That is the whole reason this folder exists.

There is a second reason, and it is why the first attempt failed silently.
Application forms score their visitor with an invisible bot check. A browser
running in a datacentre fails that score and the application is refused with
nothing shown on screen. Running it here, in your own Chrome, passes for the
plainest possible reason: it really is you.

## Setup, once

1. Double-click **setup.bat**. It installs what is needed and makes hunter start
   with Windows.
2. Copy **env.bat.example** to **env.bat** and paste in your two Supabase values.
3. Double-click **hunter-watch.bat**.

Leave that window open. Minimise it and forget it.

## What you do from then on

1. An application email arrives. Read it. The whole form is in the email,
   including a picture of it filled in.
2. Reply **APPROVE** on its own line.
3. Within a minute, Chrome opens on the real form, filled in, with your CV
   attached.
4. Read it and press **Submit**.

Nothing else. The employer's "thanks for applying" email closes the row in your
sheet by itself, moves it off Pipeline onto Applied, and tells you it is done.

## Things worth knowing

**Hunter never presses Submit.** Not here, not in the cloud. The code that opens
the form holds no reference to the submit button; there is a test that reads the
compiled function to prove it. The last click on a job application is yours.

**If Chrome is already open**, hunter uses it rather than starting another.

**If nothing opens**, the window will say why. The usual causes are Chrome not
being installed where it is expected (set `HUNTER_CHROME_PATH` in env.bat), or
no application actually being approved yet.

**To stop it**, close the window. To stop it starting with Windows, delete
`hunter-watch.bat` from your Startup folder (press Win+R, type `shell:startup`).
