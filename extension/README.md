# Hunter form filler

Install this once. After that, an application email has a green **Open the
filled form** button: press it and the real application opens with every field
filled and your CV attached, for you to read and press Submit.

## Install, once

1. Download this `extension` folder to your PC.
2. Open Chrome and go to `chrome://extensions`
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and choose the folder.

That is all. There is nothing to configure.

## Update it, when the bar tells you to

Chrome never updates an extension loaded this way, so this folder stays exactly
as you downloaded it while hunter moves on. When an application needs something
newer than the copy you have, the bar at the top turns red and says so by
version number. Then:

1. Download <https://github.com/krishanraja/hunter/archive/refs/heads/main.zip>
2. Extract it over your `hunter` folder, replacing the files.
3. At `chrome://extensions`, press the **reload** arrow on the Hunter card.
4. Reopen the link from the email.

You will not have to guess. An out of date copy says so instead of quietly
leaving part of the form empty.

## What it does

When you open a link that carries a hunter capability, it fetches that one
application, fills the form, attaches your documents, and puts a bar at the top
of the page telling you what it filled and what it could not. Then it stops.

**It never presses Submit.** There is no reference to a submit control anywhere
in the code, and a test reads the files to prove it. The last click on a job
application is yours.

## Why an extension at all

A web page is not allowed to attach a file to a form. That is a browser security
rule: if a page could do it, any website could quietly upload your documents. So
no link and no button in an email can ever hand you a form with your CV already
on it.

An extension can, because you installed it deliberately and it only runs on the
two job boards listed in its manifest. It cannot see the rest of your browsing.

## The bar at the top

- **green** everything went in, read it and press Submit
- **grey** something optional was left, named, and you can ignore it or fill it
- **red** either something REQUIRED could not be filled, and it is named, or
  this copy of the extension is out of date and needs the reload above

## If nothing happens

The link has to be the one from the email, with the `#hunter=...` part intact.
Some mail clients strip it if you copy the text rather than pressing the button.
