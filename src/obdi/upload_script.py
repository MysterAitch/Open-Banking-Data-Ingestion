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

  function send(file, sent, total) {
    return new Promise(function (resolve) {
      var began = (window.performance || Date).now();
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

  function cell(row, text, mono) {
    var td = document.createElement('td');
    if (mono) td.className = 'mono';
    td.textContent = text;
    row.appendChild(td);
  }

  // What each file cost, measured HERE rather than on the server, because
  // the server cannot see the time its reply spends reaching this page or
  // the time the file spent getting there. The server's own phase
  // breakdown travels in the last column, so the two accounts sit beside
  // each other and their difference is the network.
  function timingsTable(measured) {
    var seconds = measured.reduce(function (sum, m) { return sum + m.seconds; }, 0);
    var bytes = measured.reduce(function (sum, m) { return sum + m.bytes; }, 0);

    var heading = document.createElement('p');
    heading.textContent = 'What it cost, per file';
    progress.appendChild(heading);

    var table = document.createElement('table');
    var head = document.createElement('tr');
    ['File', 'Size', 'Round trip', 'Server phases'].forEach(function (name) {
      var th = document.createElement('th');
      th.textContent = name;
      head.appendChild(th);
    });
    table.appendChild(head);
    measured.forEach(function (m) {
      var row = document.createElement('tr');
      cell(row, m.name, false);
      cell(row, sizeOf(m.bytes), true);
      cell(row, m.seconds.toFixed(2) + 's', true);
      cell(row, m.server || '-', true);
      table.appendChild(row);
    });
    progress.appendChild(table);

    // Throughput, because on this link it is the figure that explains the
    // duration - and it has been measured varying six-fold between one
    // attempt and the next, so a single past reading predicts nothing.
    if (seconds > 0) {
      var mbps = (bytes * 8) / seconds / 1000000;
      block(sizeOf(bytes) + ' in ' + seconds.toFixed(1) + 's - ' +
            mbps.toFixed(2) + ' Mbps, this attempt.', 'muted');
    }
  }

  // Every outcome gets its own line, strongest first, each one able to
  // stand alone. The headline says what happened before any detail, so a
  // glance is enough and reading further is a choice rather than the only
  // way to find out whether anything went wrong.
  function report(outcome) {
    progress.innerHTML = '';
    var worked = outcome.sending - outcome.failed.length;

    if (outcome.failed.length) {
      block(worked + ' of ' + outcome.sending + ' file(s) kept. ' +
            outcome.failed.length + ' FAILED.', 'bad');
    } else {
      block('All ' + worked + ' file(s) kept (' + sizeOf(outcome.total) + ').',
            'ok');
    }

    if (outcome.stopped) {
      block('Stopped early: three in a row failed, so the rest were not ' +
            'attempted. That usually means the server became unreachable ' +
            'rather than anything being wrong with the files.', 'warn');
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

    var all = document.createElement('p');
    var index = document.createElement('a');
    index.href = '/artefacts';
    index.textContent = 'Every statement kept so far';
    all.appendChild(index);
    progress.appendChild(all);
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
        say('All ' + files.length + ' file(s) already held - nothing to send. ' +
            'Tick the override to send them anyway.');
        return;
      }

      var kept = [];
      var failed = [];
      var measured = [];
      var sent = 0;
      var consecutive = 0;
      var stopped = false;
      var chain = Promise.resolve();
      sending.forEach(function (file) {
        chain = chain.then(function () {
          // Once several in a row have failed the cause is almost never
          // the individual file - the destination has gone away - and
          // sending the rest just produces a longer list of the same
          // failure while the person waits for it.
          if (stopped) return;
          return send(file, sent, total).then(function (result) {
            sent += file.size;
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
