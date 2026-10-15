import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { serialize } from 'node:v8';
import vm from 'node:vm';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const script = readFileSync(`${root}scripts/patch_g6_validation.py`, 'utf8');
function constant(name) {
  const match = script.match(new RegExp(`^${name} = (?:b)?(".*")$`, 'm'));
  assert.ok(match, `pinned patch constant ${name}`);
  return JSON.parse(match[1]);
}
const originalFragment = constant('ORIGINAL'), patchedFragment = constant('PATCHED');
const source = readFileSync(`${root}static/vendor/g6.min.js`, 'utf8');
assert.equal(createHash('sha256').update(source).digest('hex'), constant('PATCHED_SHA256'));
assert.equal(source.split(patchedFragment).length - 1, 1);
assert.equal(source.includes(originalFragment), false);
const marker = 'Ze=function(t){if(!t)return console.error("G6 Error Tips: the data must be defined")';
assert.equal(source.split(marker).length - 1, 1);
const start = source.indexOf(marker), end = source.indexOf('},Ke=function', start);
assert.ok(end > start, 'extract the actual pinned validator, not a rewritten implementation');
const patchedFunction = source.slice(start + 'Ze='.length, end + 1);
assert.equal(patchedFunction.split(patchedFragment).length - 1, 1);
const originalFunction = patchedFunction.replace(patchedFragment, originalFragment);

function compile(functionSource, forbidIncludes = false) {
  const context = vm.createContext({});
  // Minimal dependencies match the bundled @antv/util, tslib and traverseTree.
  vm.runInContext(`
    globalThis.messages=[];
    globalThis.console={warn:(...args)=>messages.push(['warn',...args]),error:(...args)=>messages.push(['error',...args])};
    globalThis.v={isString:value=>Object.prototype.toString.call(value)==='[object String]'};
    globalThis.g={__spreadArray:function(to,from,pack){
      if(pack||arguments.length===2)for(var copy,index=0,length=from.length;index<length;index++){
        if(copy||!(index in from)){if(!copy)copy=Array.prototype.slice.call(from,0,index);copy[index]=from[index];}
      }
      return to.concat(copy||Array.prototype.slice.call(from));
    }};
    globalThis.Be=function(tree,callback){
      if(typeof callback!=='function')return;
      (function visit(node,parent,index){
        if(callback(node,parent,index)===false)return false;
        if(node&&node.children)for(var i=node.children.length-1;i>=0;i--){
          if(visit(node.children[i],node,i)===false)return false;
        }
        return true;
      })(tree,null,-1);
    };
    globalThis.Ze=(${functionSource});
    ${forbidIncludes ? "Array.prototype.includes=function(){throw new Error('linear Array.includes reached')};" : ''}
  `, context);
  return context;
}

function evaluate(functionSource, expression, forbidIncludes = false) {
  const context = compile(functionSource, forbidIncludes);
  vm.runInContext(`globalThis.input=(${expression})`, context);
  const before = serialize(context.input);
  let value, error;
  try { value = vm.runInContext('Ze(input)', context); }
  catch (caught) { error = { name: caught.name, message: caught.message }; }
  assert.deepEqual(serialize(context.input), before, 'validation must not alter supplied nodes, edges, metadata or references');
  return { value, error, messages: JSON.parse(JSON.stringify(context.messages)) };
}

const fixtures = [
  ['empty graph', `{nodes:[],edges:[]}`, true],
  ['nodes without edges', `{nodes:[{id:'00001'}]}`, true],
  ['edges without nodes may use combo endpoints', `{edges:[{source:'C',target:'C'}],combos:[{id:'C'}]}`, true],
  ['normal graph and self-loop', `{nodes:[{id:'00001'},{id:'00002'}],edges:[{source:'00001',target:'00002'},{source:'00001',target:'00001'}]}`, true],
  ['node and combo endpoints', `{nodes:[{id:'N'}],combos:[{id:'C'}],edges:[{source:'N',target:'C'},{source:'C',target:'N'}]}`, true],
  ['numeric combo IDs keep upstream membership behavior', `{nodes:[{id:'N'}],combos:[{id:7}],edges:[{source:'N',target:7}]}`, true],
  ['string and numeric IDs remain distinct', `{nodes:[{id:'7'}],edges:[{source:7,target:'7'}]}`, false],
  ['NaN combo ID uses SameValueZero', `{nodes:[],combos:[{id:NaN}],edges:[{source:NaN,target:NaN}]}`, true],
  ['negative zero and zero remain equal', `{nodes:[],combos:[{id:-0}],edges:[{source:0,target:-0}]}`, true],
  ['object combo identity is preserved', `(()=>{const id={synthetic:true};return {nodes:[],combos:[{id}],edges:[{source:id,target:id}]}})()`, true],
  ['equivalent object is not the same combo ID', `(()=>{const id={synthetic:true};return {nodes:[],combos:[{id}],edges:[{source:{synthetic:true},target:id}]}})()`, false],
  ['boxed strings retain AntV isString behavior', `(()=>{const id=new String('N');return {nodes:[{id}],edges:[{source:id,target:id}]}})()`, true],
  ['duplicate IDs remain a later renderer concern', `{nodes:[{id:'N'},{id:'N'}],combos:[{id:'N'},{id:'N'}],edges:[{source:'N',target:'N'}]}`, true],
  ['numeric node ID is still rejected', `{nodes:[{id:7}],edges:[]}`, false],
  ['missing node ID is still rejected', `{nodes:[{}],edges:[]}`, false],
  ['empty string ID remains accepted by this validator', `{nodes:[{id:''}],edges:[{source:'',target:''}]}`, true],
  ['dangling source is rejected', `{nodes:[{id:'N'}],edges:[{source:'MISSING',target:'N'}]}`, false],
  ['dangling target is rejected', `{nodes:[{id:'N'}],edges:[{source:'N',target:'MISSING'}]}`, false],
  ['missing edge endpoint remains rejected', `{nodes:[{id:'N'}],edges:[{source:'N'}]}`, false],
  ['tree branch with descendants', `{id:'ROOT',children:[{id:'CHILD',children:[{id:'LEAF'}]}]}`, true],
  ['tree branch invalid descendant', `{id:'ROOT',children:[{id:9}]}`, false],
  ['missing tree root ID', `{}`, false],
  ['undefined input', `undefined`, false],
  ['null input', `null`, false],
  ['false input', `false`, false],
  ['malformed node list preserves the upstream exception', `{nodes:[null],edges:[]}`, undefined],
  ['malformed combo list preserves the upstream exception', `{nodes:[],edges:[],combos:null}`, undefined],
  ['frozen facts and exact amounts stay unchanged', `(()=>{const totals=Object.freeze([{currency:'CNY',amount:'10000000000000000.01'}]);return Object.freeze({nodes:Object.freeze([Object.freeze({id:'00001'}),Object.freeze({id:'00002'})]),edges:Object.freeze([Object.freeze({source:'00001',target:'00002',amount_totals:totals,total:'10000000000000000.01'})])})})()`, true],
];

for (const [name, expression, expected] of fixtures) {
  test(name, () => {
    const original = evaluate(originalFunction, expression), patched = evaluate(patchedFunction, expression);
    assert.deepEqual(patched, original, 'same return, thrown error and complete warning arguments as the original artifact');
    assert.equal(patched.value, expected);
    if (expected === undefined) assert.ok(patched.error);
    else assert.equal(patched.error, undefined);
  });
}

test('the first invalid node and edge retain the complete upstream diagnostic', () => {
  const node = evaluate(patchedFunction, `{nodes:[{id:7},{id:8}],edges:[]}`);
  assert.equal(node.messages.length, 1);
  assert.match(node.messages[0][1], /%c7%c is not a string/);
  const expression = `{nodes:[{id:'N'}],edges:[{source:'FIRST',target:'N'},{source:'N',target:'SECOND'}]}`;
  const patched = evaluate(patchedFunction, expression);
  assert.deepEqual(patched, evaluate(originalFunction, expression));
  assert.equal(patched.messages.length, 1);
  assert.match(patched.messages[0][1], /source %cFIRST%c or the target %cN%c/);
  assert.equal(patched.messages[0].length, 6);
});

test('the extracted patched validator cannot scan Array.prototype.includes', () => {
  const expression = `{nodes:Array.from({length:256},(_,i)=>({id:String(i)})),edges:Array.from({length:255},(_,i)=>({source:String(i),target:String(i+1)}))}`;
  const patched = evaluate(patchedFunction, expression, true);
  assert.equal(patched.value, true); assert.equal(patched.error, undefined);
  const original = evaluate(originalFunction, expression, true);
  assert.equal(original.error?.message, 'linear Array.includes reached');
});

test('valid graph membership performs exactly two Set.has checks per edge', () => {
  const context = compile(patchedFunction);
  vm.runInContext(`globalThis.hasCalls=0;globalThis.Set=class extends Set{has(value){hasCalls++;return super.has(value)}};
    globalThis.input={nodes:Array.from({length:64},(_,i)=>({id:String(i)})),
      edges:Array.from({length:127},(_,i)=>({source:String(i%64),target:String((i+1)%64)}))};`, context);
  assert.equal(vm.runInContext('Ze(input)', context), true);
  assert.equal(context.hasCalls, 254);
});

test('the actual Graph.data setter keeps its upstream warning-and-set behavior', () => {
  const marker = '.prototype.data=function(t){Ze(t),this.set("data",t)}';
  assert.equal(source.split(marker).length - 1, 1);
  const setter = marker.slice('.prototype.data='.length);
  const context = compile(patchedFunction);
  vm.runInContext(`globalThis.setter=(${setter});globalThis.input={nodes:[{id:9}],edges:[]};
    globalThis.graph={set(key,value){this[key]=value}};setter.call(graph,input);`, context);
  assert.equal(context.graph.data, context.input);
  assert.equal(context.messages[0][0], 'warn');
});
