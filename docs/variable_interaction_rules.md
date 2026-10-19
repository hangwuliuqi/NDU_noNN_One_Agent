# variable interaction trend rule specification

## 1. purpose

this guidebook describes qualitative trends for coordinated antenna-variable changes. a rule is an experience hypothesis inferred from the supplied samples. it is not a universal physical law and is not expected to hold at every point in its domain.

the experience llm directly produces the rule content and its confidence. deterministic code only validates json structure, legal variables, continuous ranges, effect symbols, and the numerical confidence range. code does not keep a validation ledger, calculate a posterior, or automatically promote and demote rules.

## 2. rule fields and their sources

the persisted rule structure is:

```text
rule
domain
var_change
effect
confidence
source
note
created_iteration
```

the experience llm produces:

```text
domain
var_change
effect
confidence
note
```

code adds only:

```text
rule
source
created_iteration
```

`source` records the sample ids supplied as context. it does not mean that every listed sample independently proves the rule.

## 3. continuous domain

`domain` defines the starting geometry for which a trend may apply.

```text
domain={l1:[26.40,34.20],s1:[0.55,0.88]}
```

this means:

```text
26.40 <= l1 <= 34.20
0.55 <= s1 <= 0.88
```

variables omitted from `domain` are not restricted by that rule. a trend supported across the complete legal design space may use:

```text
domain=global
```

no fixed low/mid/high partition is used. the experience llm infers continuous boundaries from the supplied evidence, and code checks that the boundaries remain inside the configured global ranges.

## 4. coordinated variable change

the allowed variable-change symbols are:

| symbol | meaning |
|---|---|
| `u` | increase the variable |
| `d` | decrease the variable |
| `k` | keep the variable approximately unchanged |

example:

```text
var_change={l1:u,s1:d,s2:k}
```

the complete mapping is one coordinated action. it must not be split into independent single-variable claims unless separate evidence supports those claims.

## 5. required effects

every rule contains exactly four effects:

```text
low_resonance_point
s11_at_2_45
high_resonance_point
s11_at_4_50
```

the two metric types use different symbols because they describe different physical changes.

### 5.1 resonance-point movement

`low_resonance_point` and `high_resonance_point` use:

| symbol | meaning |
|---|---|
| `l` | the numerical resonance frequency moves lower |
| `h` | the numerical resonance frequency moves higher |
| `k` | no clear meaningful resonance-frequency movement |

examples:

```text
2.60 ghz -> 2.50 ghz = l
2.30 ghz -> 2.40 ghz = h
4.50 ghz -> 4.50 ghz = k
```

these symbols describe physical movement direction, not whether the movement improves the design target. for example, a low-band resonance moving from 2.60 ghz to 2.50 ghz is `l` and also happens to move closer to 2.45 ghz. a movement from 2.40 ghz to 2.30 ghz is also `l`, but it moves away from 2.45 ghz.

### 5.2 fixed-target s11 depth

`s11_at_2_45` and `s11_at_4_50` use:

| symbol | meaning |
|---|---|
| `d` | s11 becomes deeper, meaning more negative |
| `s` | s11 becomes shallower, meaning less negative |
| `k` | no clear meaningful s11-depth change |

examples:

```text
-7 db -> -13 db = d
-15 db -> -8 db = s
-12 db -> -12 db = k
```

the ranking score combines actual resonance-position quality with target-frequency s11 quality. It is used only for ranking and TOP-Q selection; the physical stopping predicate separately checks two resonances, both frequency tolerances, and both target-frequency s11 thresholds.

### 5.3 missing resonance evidence

the llm input uses:

```text
{n_res:0}+{res:[]}
{n_res:1}+{res:[[4.48,-15.25]]}
{n_res:2}+{res:[[2.48,-15.40],[4.47,-15.70]]}
```

an omitted resonance is unknown evidence. it must not be interpreted as `l`, `h`, or `k`.

## 6. confidence

`confidence` is a number from `0.00` to `1.00` returned directly by the experience llm after reading the current score-ranked TOP-Q real samples and the previous rule snapshot. sampling-llm proposals, rationales, and cited rule IDs are not supplied to the experience llm.

it is an evidence assessment, not a mathematically calibrated probability. confidence must be between `0` and `1`; never use `1.00`. code checks only that it is finite and inside `[0,1]`, then rounds it to two decimal places. code never increases or decreases it automatically.

recommended interpretation:

| range | interpretation |
|---|---|
| `0.00–0.39` | weak or highly confounded hypothesis |
| `0.40–0.59` | limited but usable evidence |
| `0.60–0.79` | multiple reasonably consistent comparisons |
| `0.80–0.95` | repeated and strongly consistent real-hfss evidence |

the experience llm should not output `1.00`, because the supplied finite samples cannot establish a universal physical law.

## 7. simple replacement workflow

after every real evaluation, the experience llm receives the previous rule snapshot and returns the complete rule set for the next iteration. it may retain, revise, or remove previous rules and may add a new rule when evidence warrants it.

code then:

1. validates every returned rule;
2. assigns a new rule id and source sample ids;
3. replaces `runtime/experience_rule_snapshot.json` with this snapshot;
4. immediately rebuilds `runtime/variable_design_guidebook.md`.

there is no separate evidence cache, beta posterior, automatic status machine, delayed publication, or near-duplicate maintenance layer.

## 8. compact sampling format

the sampling llm receives rules in this form:

```text
rule=r-0007-01
domain={l1:[26.40,34.20],s1:[0.55,0.88]}
var_change={l1:u,s1:d}
effect={low_resonance_point:h,s11_at_2_45:d,high_resonance_point:l,s11_at_4_50:s}
confidence=0.68
note=in this continuous starting domain, increasing l1 while decreasing s1 tends to move the low resonance higher, deepen s11 at 2.45 ghz, move the high resonance lower, and make s11 at 4.50 ghz shallower.
```

## 9. experience llm output

the experience llm returns the complete next-iteration rule snapshot:

```json
{
  "summary": "short evidence-bounded assessment",
  "rules": [
    {
      "domain": {
        "l1": {"min": 26.40, "max": 34.20},
        "s1": {"min": 0.55, "max": 0.88}
      },
      "var_change": {"l1": "u", "s1": "d"},
      "effect": {
        "low_resonance_point": "h",
        "s11_at_2_45": "d",
        "high_resonance_point": "l",
        "s11_at_4_50": "s"
      },
      "confidence": 0.68,
      "note": "short english statement of the observed trend and its evidence limits"
    }
  ]
}
```
