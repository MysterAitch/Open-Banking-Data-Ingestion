"""The browser-side half of keeping statements, as progressive enhancement.

The form underneath works without any of this. That is the point: the
script makes a slow upload visible and shorter, and if it does not run -
scripting off, a hashing API unavailable outside a secure context, an
error in the page - the plain form still posts the files the ordinary way.
A faster path that becomes the ONLY path is a regression for whoever it
does not work for.

It does three things the form cannot.

It ASKS FIRST. The store keys artefacts by content, so hashing a file
locally answers "would sending this tell you anything new?" for 64
characters instead of megabytes. On the batch that prompted this, 18 of 31
files were documents already held and nearly 12 MiB was sent so the server
could recognise something it already had.

It SHOWS PROGRESS. Half a minute of silence was read as a hang once
already, and the reading was reasonable: nothing on the page distinguished
the two.

It DROPS WHAT IS NOT A STATEMENT, saying how many. A folder of statements
holds CSV exports too, and selecting the folder should not mean sending
them to a door that only reads PDFs.

It SENDS ONE FILE AT A TIME. A single request carrying everything runs
long enough to meet a proxy or browser timeout, and losing it loses the
whole batch. One request per file means a failure costs that file, results
appear as they land, and stopping half way keeps everything so far.
"""

from __future__ import annotations

#: Served inline. No CDN, no build step - the page has one dependency and
#: it is the browser.
UPLOAD_SCRIPT = r"""
(function () {
  var form = document.querySelector('form[action="/statement-shape"]');
  if (!form) return;
  var input = form.querySelector('input[type=file]');
  var progress = document.getElementById('progress');
  var force = document.getElementById('force');
  if (!input || !progress) return;

  // Everything below needs all three. Without them the form is left
  // exactly as it is, which still works.
  if (!window.crypto || !window.crypto.subtle || !window.fetch ||
      !window.XMLHttpRequest) {
    return;
  }

  function say(text) { progress.textContent = text; }

  function sizeOf(bytes) {
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MiB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KiB';
    return bytes + ' B';
  }

  function digestOf(file) {
    return file.arrayBuffer().then(function (buffer) {
      return crypto.subtle.digest('SHA-256', buffer);
    }).then(function (hash) {
      var out = [];
      new Uint8Array(hash).forEach(function (byte) {
        out.push(byte.toString(16).padStart(2, '0'));
      });
      return out.join('');
    });
  }

  // Answers which digests the store already holds. A failure here is not
  // fatal and must not be silent: uploading everything is merely slow,
  // whereas pretending nothing is held would be a claim we cannot make.
  function heldAmong(digests) {
    return fetch('/statement-held', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ digests: digests })
    }).then(function (response) {
      if (!response.ok) return null;
      return response.json();
    }).then(function (body) {
      return body ? body.held : null;
    }).catch(function () { return null; });
  }

  function send(file, sent, total) {
    return new Promise(function (resolve) {
      var data = new FormData();
      data.append('file', file);
      var request = new XMLHttpRequest();
      request.open('POST', '/statement-shape');
      request.upload.addEventListener('progress', function (event) {
        if (!event.lengthComputable) return;
        var done = sent + event.loaded;
        say('Sending ' + file.name + ' - ' + sizeOf(done) + ' of ' +
            sizeOf(total) + ' (' + Math.floor((done / total) * 100) + '%)');
      });
      request.addEventListener('load', function () {
        resolve({ ok: request.status === 200, body: request.responseText });
      });
      request.addEventListener('error', function () {
        resolve({ ok: false, body: '' });
      });
      request.send(data);
    });
  }

  // The response is the ordinary result page, so the statement ids it
  // links to are read back out of it rather than duplicated into a second
  // format the server would have to keep in step.
  function statementsIn(body) {
    var found = [];
    try {
      var page = new DOMParser().parseFromString(body, 'text/html');
      page.querySelectorAll('a[href*="statement-shape?artefact="]')
        .forEach(function (link) { found.push(link.textContent.trim()); });
    } catch (error) { /* a result we cannot read is not a failure to send */ }
    return found;
  }

  // A directory selection arrives as every file underneath it, so the
  // ones that are not statements are dropped here rather than sent and
  // refused. Named by extension as well as by type: a browser handed a
  // whole folder often reports no type at all.
  function isStatement(file) {
    if (file.type === 'application/pdf') return true;
    return /\.pdf$/i.test(file.name || '');
  }

  // Set when the enhanced path has failed. The next submit is then left
  // entirely alone, so the form posts natively. Without this the listener
  // would keep calling preventDefault and the advice to "try again with
  // the plain form" would be impossible to follow - the enhancement would
  // have become the only path by failing, which is the one outcome
  // progressive enhancement exists to prevent.
  var standDown = false;

  form.addEventListener('submit', function (event) {
    if (standDown) return;
    var chosen = Array.prototype.slice.call(input.files || []);
    var picker = document.getElementById('folder');
    if (picker && picker.files && picker.files.length) {
      chosen = chosen.concat(Array.prototype.slice.call(picker.files));
    }
    if (!chosen.length) return;
    event.preventDefault();

    var files = chosen.filter(isStatement);
    var ignored = chosen.length - files.length;
    if (!files.length) {
      say('None of the ' + chosen.length + ' file(s) chosen is a PDF.');
      return;
    }

    var forcing = force && force.checked;
    say('Hashing ' + files.length + ' file(s)...');

    Promise.all(files.map(digestOf)).then(function (digests) {
      // The override skips the asking, not the sending: nothing is held
      // as far as this run is concerned.
      if (forcing) return { skip: {}, checked: true, digests: digests };
      return heldAmong(digests).then(function (held) {
        if (held === null) return { skip: {}, checked: false };
        var skip = {};
        held.forEach(function (digest) { skip[digest] = true; });
        return { skip: skip, checked: true };
      }).then(function (answer) {
        answer.digests = digests;
        return answer;
      });
    }).then(function (answer) {
      var digests = answer.digests || [];
      var sending = files.filter(function (_file, index) {
        return !answer.skip[digests[index]];
      });
      var skipped = files.length - sending.length;
      var total = sending.reduce(function (sum, f) { return sum + f.size; }, 0);

      if (!answer.checked && !forcing) {
        say('Could not check what is already held, so sending everything - ' +
            sizeOf(total) + '.');
      }
      if (!sending.length) {
        say('All ' + files.length + ' file(s) already held - nothing to send. ' +
            'Tick the override to send them anyway.');
        return;
      }

      var kept = [];
      var failed = [];
      var sent = 0;
      var chain = Promise.resolve();
      sending.forEach(function (file) {
        chain = chain.then(function () {
          return send(file, sent, total).then(function (result) {
            sent += file.size;
            if (result.ok) {
              statementsIn(result.body).forEach(function (id) {
                if (kept.indexOf(id) === -1) kept.push(id);
              });
            } else {
              failed.push(file.name);
            }
          });
        });
      });

      return chain.then(function () {
        // Counts with their denominator, and the failures NAMED - a
        // summary that reports only successes reads identically whether
        // or not anything went wrong.
        var lines = [
          'Sent ' + (sending.length - failed.length) + ' of ' +
          sending.length + ' file(s), ' + sizeOf(total) + '.'
        ];
        if (skipped) {
          lines.push(skipped + ' already held, not sent.');
        }
        if (ignored) {
          // Named rather than dropped quietly: a person who chose a
          // folder is entitled to know what was left out of it.
          lines.push(ignored + ' of ' + chosen.length +
                     ' file(s) chosen were not PDFs and were ignored.');
        }
        if (failed.length) {
          lines.push('FAILED: ' + failed.join(', ') +
                     ' - these were not kept. Try them again.');
        }
        if (kept.length) {
          lines.push('statements ' + kept.join(', '));
        }
        progress.innerHTML = '';
        lines.forEach(function (line) {
          var p = document.createElement('p');
          p.textContent = line;
          progress.appendChild(p);
        });
      });
    }).catch(function (error) {
      standDown = true;
      say('Upload could not be completed in the browser (' + error +
          '). Press the button again - it will now send the files the ' +
          'plain way, which needs no scripting.');
    });
  });
})();
"""
