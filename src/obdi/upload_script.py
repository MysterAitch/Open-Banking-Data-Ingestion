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

  // Appends rather than replaces. Each stage used to overwrite the last,
  // so "hashing 32 files" flashed past and the answer that followed it -
  // mentioning 31 - looked like a contradiction with no way to check,
  // because the sentence that would have explained it had already gone.
  // A run leaves a record of itself, and so does the run before it.
  function say(text, className) {
    var line = document.createElement('p');
    line.className = className || 'muted mono';
    line.textContent = text;
    progress.appendChild(line);
    return line;
  }

  // Progress is the one thing that SHOULD overwrite: a percentage is
  // only ever interesting as its latest value, and appending one line per
  // event would bury everything else under thousands of them.
  var ticker = null;
  var runBegan = 0;
  function tick(text) {
    if (!ticker || !ticker.parentNode) ticker = say('', 'muted mono');
    ticker.textContent = text;
  }

  function beginRun(what) {
    if (progress.childNodes.length) {
      progress.appendChild(document.createElement('hr'));
    }
    say(what, '');
    // The running total is created HERE rather than at first use, so it
    // holds one position under the heading while the per-file lines append
    // beneath it. Created lazily it landed wherever the first progress
    // event happened to fire - between the first and second file - and a
    // summary that moves is one a reader has to find again each time.
    ticker = say('', 'muted mono');
    runBegan = (window.performance || Date).now();
  }

  // Units follow magnitude, and below the resolution the answer is a
  // BOUND. "0.00s" asserts that work which definitely happened took no
  // time, and "0%" asserts it was none of the total - both are what
  // rounding did to a small number, not what was measured.
  function duration(seconds) {
    if (seconds >= 1) return seconds.toFixed(2) + 's';
    if (seconds >= 0.01) return Math.round(seconds * 1000) + 'ms';
    if (seconds >= 0.0001) return (seconds * 1000).toFixed(1) + 'ms';
    return '<0.01ms';
  }

  function shareOf(part, whole) {
    if (!(whole > 0)) return '-';
    var percent = (part / whole) * 100;
    if (percent >= 1) return Math.round(percent) + '%';
    if (percent >= 0.1) return percent.toFixed(1) + '%';
    return '<0.1%';
  }

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

  // The server renders its own phase breakdown on the result page, which
  // this script never navigates to - so it is read back out of the reply
  // rather than being reported in a second format the server would have
  // to keep in step with the first.
  function timingsIn(body) {
    try {
      var page = new DOMParser().parseFromString(body, 'text/html');
      var found = page.getElementById('timings');
      return found ? found.textContent.replace(/^timings:\s*/, '') : '';
    } catch (error) { return ''; }
  }

  // One line per file, kept. A single line rewritten in place showed only
  // whichever file was in flight, so a batch of thirty scrolled past as one
  // changing sentence and left no record of which files had been dealt with
  // - the question actually being asked while waiting.
  function fileLine(position, count, file) {
    var line = say('', 'muted mono');
    line.textContent = 'file ' + position + '/' + count + ' ' + file.name +
                       ' - 0 B of ' + sizeOf(file.size);
    return line;
  }

  function send(file, sent, total, line, position, count) {
    return new Promise(function (resolve) {
      var began = (window.performance || Date).now();
      var data = new FormData();
      data.append('file', file);
      var request = new XMLHttpRequest();
      request.open('POST', '/statement-shape');
      request.upload.addEventListener('progress', function (event) {
        if (!event.lengthComputable) return;
        // What goes over the wire is the file PLUS the multipart framing
        // around it, so raw progress passes the total and reports more
        // than all of it - 109% of 2 KiB was on screen. A share of
        // something cannot exceed the something.
        var mine = Math.min(event.loaded, file.size);
        var done = Math.min(sent + event.loaded, total);
        if (line) {
          line.textContent = 'file ' + position + '/' + count + ' ' +
                             file.name + ' - ' + sizeOf(mine) + ' of ' +
                             sizeOf(file.size);
        }
        // The overall figure stays in ONE place that overwrites, because a
        // running total is only ever interesting as its latest value.
        tick('Overall: ' + position + ' of ' + count + ' file(s), ' +
             sizeOf(done) + ' of ' + sizeOf(total) +
             ' (' + Math.floor((done / total) * 100) + '%), ' +
             // Elapsed so far rather than an estimate of what remains: the
             // link has been measured varying six-fold between consecutive
             // attempts, so any prediction made from it would be fiction.
             duration(((window.performance || Date).now() - runBegan) / 1000) +
             ' so far');
      });
      request.addEventListener('load', function () {
        resolve({
          ok: request.status === 200,
          body: request.responseText,
          seconds: ((window.performance || Date).now() - began) / 1000,
          server: timingsIn(request.responseText),
          // The reason travels with the failure. "It did not work" sends
          // somebody looking at their files; "the server said 500" and
          // "there was no reply at all" send them somewhere useful.
          why: request.status === 200 ? '' : 'the server replied ' +
               request.status + ' ' + (request.statusText || '')
        });
      });
      request.addEventListener('error', function () {
        resolve({ ok: false, body: '', why: 'no reply - connection lost' });
      });
      request.addEventListener('timeout', function () {
        resolve({ ok: false, body: '', why: 'timed out waiting for a reply' });
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

  function block(text, className) {
    var p = document.createElement('p');
    if (className) p.className = className;
    p.textContent = text;
    progress.appendChild(p);
    return p;
  }

  function listOf(items, limit) {
    // A list, not a sentence. Names separated by commas and wrapped into a
    // paragraph have to be READ to be counted, and anything said at the
    // end of one - including a sentence that reverses its meaning - is
    // indistinguishable from more of the same.
    var list = document.createElement('ul');
    items.slice(0, limit).forEach(function (item) {
      var entry = document.createElement('li');
      entry.textContent = item;
      list.appendChild(entry);
    });
    progress.appendChild(list);
    if (items.length > limit) {
      block((items.length - limit) + ' more not listed, of ' + items.length +
            ' altogether.', 'muted');
    }
  }

  // A stable colour per phase, so the same kind of work is the same
  // colour on every row and a shape can be recognised without reading.
  // Named phases are fixed; anything new gets a hue from its own letters
  // rather than a colour that shifts when the list changes.
  var PHASE_COLOURS = {
    receive: '#2563eb', open: '#7c3aed', text: '#dc2626',
    mask: '#ea580c', geometry: '#0891b2', keep: '#16a34a',
    unaccounted: '#94a3b8'
  };

  function colourFor(name) {
    if (PHASE_COLOURS[name]) return PHASE_COLOURS[name];
    var hue = 0;
    for (var i = 0; i < name.length; i += 1) {
      hue = (hue * 31 + name.charCodeAt(i)) % 360;
    }
    return 'hsl(' + hue + ', 55%, 45%)';
  }

  // "geometry 0.17s | receive 0.04s | text 0.04s" makes the eye chase
  // numbers across a line and compare them by reading. The same figures
  // as proportional blocks can be compared by LOOKING - which phase
  // dominates is the shape of the bar, not an arithmetic exercise.
  function parsePhases(text) {
    var phases = [];
    (text || '').split('|').forEach(function (part) {
      // Seconds or milliseconds, and a bound like "<0.01ms" counts as
      // present-but-tiny rather than absent - dropping it would make the
      // shares add up to less than the whole with nothing saying why.
      // No backslash escapes in this pattern, deliberately. The
      // script is a string in a Python module served inside an HTML
      // page, so a backslash has several layers to survive - and one
      // that does not arrive turns the pattern into one that matches
      // nothing, silently, for every row at once. A class spelled out
      // in full cannot be mangled on the way here.
      var match = /([a-zA-Z.]+)[ ]+<?([0-9.]+)(ms|s)/.exec(part);
      if (!match) return;
      var value = parseFloat(match[2]);
      phases.push({
        name: match[1],
        seconds: match[3].toLowerCase() === 'ms' ? value / 1000 : value
      });
    });
    return phases;
  }

  function waterfall(phases, into) {
    var total = phases.reduce(function (sum, p) { return sum + p.seconds; }, 0);
    if (!(total > 0)) {
      // Every phase rounded to zero. Drawing nothing here leaves a gap
      // that reads as "not measured" when it means "too fast to measure",
      // and those two want opposite reactions.
      var quick = document.createElement('div');
      quick.className = 'muted mono';
      quick.textContent = 'every phase under 0.01s - nothing to divide up';
      into.appendChild(quick);
      return;
    }

    var bar = document.createElement('div');
    bar.style.display = 'flex';
    bar.style.height = '14px';
    bar.style.borderRadius = '3px';
    bar.style.overflow = 'hidden';
    bar.style.margin = '.35rem 0';
    phases.forEach(function (phase) {
      var share = (phase.seconds / total) * 100;
      if (share <= 0) return;
      var segment = document.createElement('div');
      segment.style.width = share + '%';
      segment.style.background = colourFor(phase.name);
      // The bar is the glance; the title is for anyone who wants the
      // number without leaving it.
      segment.title = phase.name + ' ' + duration(phase.seconds);
      bar.appendChild(segment);
    });
    into.appendChild(bar);

    // Numbers in a column, right-aligned, so digits line up under digits
    // and two rows can be compared down the page rather than across it.
    var table = document.createElement('table');
    phases.slice().sort(function (a, b) { return b.seconds - a.seconds; })
      .forEach(function (phase) {
        var row = document.createElement('tr');
        var key = document.createElement('td');
        var swatch = document.createElement('span');
        swatch.style.display = 'inline-block';
        swatch.style.width = '.7rem';
        swatch.style.height = '.7rem';
        swatch.style.borderRadius = '2px';
        swatch.style.background = colourFor(phase.name);
        swatch.style.marginRight = '.4rem';
        key.appendChild(swatch);
        key.appendChild(document.createTextNode(phase.name));
        row.appendChild(key);
        var value = document.createElement('td');
        value.className = 'mono';
        value.style.textAlign = 'right';
        value.style.whiteSpace = 'nowrap';
        value.textContent = duration(phase.seconds);
        row.appendChild(value);
        var portion = document.createElement('td');
        portion.className = 'mono muted';
        portion.style.textAlign = 'right';
        portion.style.whiteSpace = 'nowrap';
        portion.textContent = shareOf(phase.seconds, total);
        row.appendChild(portion);
        table.appendChild(row);
      });
    into.appendChild(table);
  }

  function timingsTable(measured) {
    var seconds = measured.reduce(function (sum, m) { return sum + m.seconds; }, 0);
    var bytes = measured.reduce(function (sum, m) { return sum + m.bytes; }, 0);

    block('What each file cost', '');
    measured.forEach(function (m) {
      var entry = document.createElement('div');
      entry.className = 'row';
      var name = document.createElement('div');
      name.textContent = m.name;
      entry.appendChild(name);
      var cost = document.createElement('div');
      cost.className = 'muted mono';
      cost.textContent = sizeOf(m.bytes) + ' - ' + duration(m.seconds) +
                         ' round trip';
      entry.appendChild(cost);
      var phases = parsePhases(m.server);
      if (phases.length) {
        waterfall(phases, entry);
      } else if (m.server) {
        var raw = document.createElement('div');
        raw.className = 'muted mono';
        raw.textContent = m.server;
        entry.appendChild(raw);
      }
      progress.appendChild(entry);
    });

    // Throughput, because on this link it is the figure that explains the
    // duration - and it has been measured varying six-fold between one
    // attempt and the next, so a single past reading predicts nothing.
    if (seconds > 0) {
      var mbps = (bytes * 8) / seconds / 1000000;
      block(sizeOf(bytes) + ' in ' + duration(seconds) + ' - ' +
            mbps.toFixed(2) + ' Mbps, this attempt.', 'muted');
    }
  }

  // Every outcome gets its own line, strongest first, each one able to
  // stand alone. The headline says what happened before any detail, so a
  // glance is enough and reading further is a choice rather than the only
  // way to find out whether anything went wrong.
  function report(outcome) {
    // Counted, not subtracted. A run that stops early leaves files
    // UNATTEMPTED, and subtracting failures from the total quietly
    // reported every one of those as kept - it once claimed 28 of 31 when
    // one had been sent. Three outcomes, and they must add up.
    var attempted = outcome.attempted || 0;
    var worked = attempted - outcome.failed.length;
    var untried = outcome.sending - attempted;

    if (!outcome.sending) {
      block('Nothing to send - every one of them is already held.', 'ok');
    } else if (outcome.failed.length) {
      block(worked + ' kept, ' + outcome.failed.length + ' FAILED, ' +
            untried + ' not attempted - of ' + outcome.sending + ' to send.',
            'bad');
    } else {
      block('All ' + worked + ' file(s) kept (' + sizeOf(outcome.total) + ').',
            'ok');
    }

    if (outcome.stopped) {
      block('Stopped after three failures in a row, so the remaining ' +
            untried + ' were not attempted. That usually means the server ' +
            'became unreachable rather than anything being wrong with the ' +
            'files - the untried ones are still worth sending.', 'warn');
    }

    if (outcome.failed.length) {
      // Grouped by REASON, because thirty files failing one way is one
      // problem and thirty failing thirty ways is thirty.
      var reasons = {};
      outcome.failed.forEach(function (item) {
        reasons[item.why] = (reasons[item.why] || 0) + 1;
      });
      Object.keys(reasons).forEach(function (why) {
        block(reasons[why] + ' failed: ' + why, 'bad');
      });
      block('Not kept - these can be sent again:', '');
      listOf(outcome.failed.map(function (item) { return item.name; }), 8);
    }

    // Where the files came from, because two boxes make a total that
    // matches neither of them and there is no way to tell from the number
    // alone which one contributed what.
    block(outcome.chosen + ' file(s) chosen: ' + outcome.picked +
          ' picked individually, ' + outcome.foldered + ' from the folder.',
          'muted');
    if (outcome.ignored) {
      block(outcome.ignored + ' were not PDFs and were ignored.', 'muted');
    }
    if (outcome.duplicated) {
      block(outcome.duplicated + ' were the same document chosen twice ' +
            '(both boxes), counted once.', 'muted');
    }
    if (outcome.skipped) {
      block(outcome.skipped + ' already held, so not sent again.', 'muted');
    }

    if (outcome.kept.length) {
      // Links, not a list of numbers. A statement that was kept is
      // something to go and look at, and the id alone makes the reader
      // construct the address themselves.
      block('Kept - open any of these to read its masked shape:', '');
      var line = document.createElement('p');
      outcome.kept.forEach(function (id, index) {
        var link = document.createElement('a');
        link.href = '/statement-shape?artefact=' + encodeURIComponent(id);
        link.textContent = id;
        if (index) line.appendChild(document.createTextNode(', '));
        line.appendChild(link);
      });
      progress.appendChild(line);
    }

    if (outcome.measured.length) {
      timingsTable(outcome.measured);
    }

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
    var picked = Array.prototype.slice.call(input.files || []);
    var picker = document.getElementById('folder');
    var foldered = picker ? Array.prototype.slice.call(picker.files || []) : [];
    var chosen = picked.concat(foldered);
    if (!chosen.length) return;
    event.preventDefault();

    var files = chosen.filter(isStatement);
    var ignored = chosen.length - files.length;
    if (!files.length) {
      say('None of the ' + chosen.length + ' file(s) chosen is a PDF.');
      return;
    }

    var forcing = force && force.checked;
    beginRun('Reading ' + files.length + ' PDF(s)...');

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
      // Choosing three files AND the folder that contains them is the
      // ordinary way to use two boxes, and it presented the same document
      // twice - counted twice, hashed twice, sent twice. The digest that
      // was computed anyway settles it.
      var seen = {};
      var unique = [];
      var uniqueDigests = [];
      files.forEach(function (file, index) {
        var digest = digests[index];
        if (digest && seen[digest]) return;
        if (digest) seen[digest] = true;
        unique.push(file);
        uniqueDigests.push(digest);
      });
      var duplicated = files.length - unique.length;
      files = unique;
      digests = uniqueDigests;

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
        report({
          sending: 0, failed: [], skipped: skipped, ignored: ignored,
          duplicated: duplicated, chosen: chosen.length, picked: picked.length,
          foldered: foldered.length, kept: [], total: 0, measured: [],
          stopped: false
        });
        block('Tick the override above to send them anyway.', 'muted');
        return;
      }

      var kept = [];
      var failed = [];
      var measured = [];
      var attempted = 0;
      var sent = 0;
      var consecutive = 0;
      var stopped = false;
      var chain = Promise.resolve();
      sending.forEach(function (file, index) {
        chain = chain.then(function () {
          // Once several in a row have failed the cause is almost never
          // the individual file - the destination has gone away - and
          // sending the rest just produces a longer list of the same
          // failure while the person waits for it.
          if (stopped) return;
          attempted += 1;
          var position = index + 1;
          var line = fileLine(position, sending.length, file);
          return send(file, sent, total, line, position, sending.length)
            .then(function (result) {
            sent += file.size;
            // The line stays, and says how it ended. A list of files with
            // no outcome against them answers "what was sent" and not
            // "what happened", which is the question being asked.
            // How long it took, on the line that says what happened to it.
            // A file that took twenty times its neighbours is the thing
            // worth noticing while a batch runs, and it cannot be noticed
            // from a total afterwards.
            line.textContent = 'file ' + position + '/' + sending.length +
                               ' ' + file.name + ' - ' + sizeOf(file.size) +
                               ' in ' + duration(result.seconds || 0) +
                               (result.ok ? ' - kept' : ' - FAILED');
            if (!result.ok) line.className = 'mono bad';
            if (result.ok) {
              consecutive = 0;
              measured.push({
                name: file.name,
                bytes: file.size,
                seconds: result.seconds || 0,
                server: result.server || ''
              });
              statementsIn(result.body).forEach(function (id) {
                if (kept.indexOf(id) === -1) kept.push(id);
              });
            } else {
              consecutive += 1;
              failed.push({ name: file.name, why: result.why });
              if (consecutive >= 3) stopped = true;
            }
          });
        });
      });

      return chain.then(function () {
        report({
          sending: sending.length,
          attempted: attempted,
          failed: failed,
          skipped: skipped,
          ignored: ignored,
          duplicated: duplicated,
          chosen: chosen.length,
          picked: picked.length,
          foldered: foldered.length,
          kept: kept,
          total: total,
          measured: measured,
          stopped: stopped
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
