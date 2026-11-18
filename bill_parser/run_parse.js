// Runs the n8n "Parse and Validate Output" code (parse_and_validate_output.js, unchanged)
// outside n8n. stdin: {"pages":[{"json":<gemini response>,"pairedItem":i}], "prep":[<page meta>...]}
// stdout: the JSON object the n8n node returned.
'use strict';
const fs = require('fs');
const path = require('path');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = fs.readFileSync(path.join(__dirname, 'parse_and_validate_output.js'), 'utf8');

const $input = { all: () => input.pages };
const $ = (name) => {
  if (name !== 'Validate Input') throw new Error('Unknown n8n node reference: ' + name);
  const items = input.prep.map((j) => ({ json: j }));
  return { all: () => items, first: () => items[0], item: items[0] };
};

const result = new Function('$input', '$', code)($input, $);
process.stdout.write(JSON.stringify(result[0].json));
