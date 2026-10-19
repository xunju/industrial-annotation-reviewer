'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const parts = require('../annotation_quality_review/model.js');
const round = require('../round_table_review/model.js');
const shape = (label, points, shape_type, group_id = null) => ({label, points, shape_type, group_id, flags: {}});
const doc = shapes => ({shapes, imagePath:'synthetic.png', imageWidth:640, imageHeight:640});

test('Ambiguous overlapping bolt boxes never borrow a corner', () => {
  const model = parts.load(doc([
    shape('screw', [[20,20],[100,100]], 'rectangle'),
    shape('screw', [[60,20],[140,100]], 'rectangle'),
    shape('top_left', [[80,40]], 'point'),
  ]));
  assert.equal(model.parts.filter(p => p.type === 'bolt').length, 2);
  assert.equal(model.unassignedBoltPoints.length, 1);
  for (const part of model.parts) assert.equal(parts.members(model, part).length, 1);
});

test('Explicit group IDs retain their own points in adjacent bolt boxes', () => {
  const model = parts.load(doc([
    shape('screw', [[20,20],[100,100]], 'rectangle', 1),
    shape('screw', [[60,20],[140,100]], 'rectangle', 2),
    shape('top_left', [[80,40]], 'point', 2),
  ]));
  const target = model.parts.find(p => parts.members(model,p).some(s => s.shape.label === 'top_left'));
  assert.equal(parts.members(model,target).find(s => s.shape.label === 'screw').shape.group_id, 2);
  assert.equal(model.unassignedBoltPoints.length, 0);
});

test('Untouched round polygon coordinates and custom metadata survive output', () => {
  const source = doc([shape('circle', [[11.25,15],[67,12],[80,55],[24,71]], 'polygon')]);
  source.flags = {custom: true};
  const output = round.output(round.load(source));
  assert.deepEqual(output.shapes, source.shapes);
  assert.deepEqual(output.flags, source.flags);
});

test('Bolt and washer review starts in whole-image mode', () => {
  const model = parts.load(doc([shape('screw', [[20,20],[100,100]], 'rectangle', 1)]));
  assert.equal(model.phase, 'overview');
  assert.equal(parts.requiresIndividualReview(model.parts[0]), false);
});
