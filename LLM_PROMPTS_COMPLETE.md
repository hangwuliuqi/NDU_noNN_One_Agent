# 两个天线优化项目的完整 LLM 输入提示词

本文根据两个项目当前的提示词模板、组装代码及最新运行日志整理。采样提示词中的设计目标、证据政策和 rationale 部分按当前已修改代码重建；TOP 样本、指导手册等动态数据来自各项目最新运行日志中最后一次成功的调用。提示词其余段落保留运行日志的完整内容。每个 prompt 均未省略样本行、规则或输出结构。

正式业务请求的 system message：

~~~text
You are a precise antenna design agent. Return only valid JSON.
~~~

项目还会在正式请求前发送 JSON 连通性预检。预检 system message：

~~~text
You are a strict JSON-only connectivity-check responder. Return exactly one valid JSON object and nothing else. Do not return a JSON array. Do not use Markdown code fences. Do not include explanations, reasoning, labels, prefixes, suffixes, or any text outside the JSON object.
~~~

预检 user message：

~~~text
Return exactly this single JSON object: {"status":"ok"}. The response must start with { and end with }. The top-level value must be an object, not an array. It must contain exactly one key named "status" whose string value is exactly "ok". Output no Markdown and no additional text.
~~~

## 1. NDU_noNN_One_Agent：采样 LLM 完整输入

取数运行：O_2026_10_12_0900_ds_two_candidate，最后成功的采样迭代：5。下面是当前提示词版本，样本数据取自该次运行最后成功采样调用。

~~~text
# role

you are the sampling guidance module in a closed-loop dual-band antenna optimization system.

# design goal

1. Use the design information in the supplied top-20 evaluated samples, which are sorted from highest to lowest by their ranking score, together with electromagnetic design knowledge. Carefully reason about how the antenna geometry parameters and their coordinated changes can affect current paths, coupling, impedance matching, and the measured S11 response. Then propose new candidate designs that aim to satisfy the design goal.
2. The target design should have two actual resonance frequencies: one between 2.44 and 2.46 GHz, and the other between 4.49 and 4.51 GHz. At each fixed target frequency, the measured S11 must be no higher than -15.00 dB.
3. The supplied `score:R` combines actual resonance-position quality and fixed-target S11 quality. It is the score used to sort and select the supplied top-20 evaluated samples from highest to lowest; it is for ranking only and is not the success condition.
4. Generate exactly 2 legal candidate designs intended to improve resonance positions and target-frequency S11 and meet the design goal after real electromagnetic evaluation.
# legal variable ranges

every generated numerical value must use two decimal places and remain inside its corresponding closed interval:

- `a`: [100.00, 150.00] mm; substrate length.
- `b`: [40.00, 80.00] mm; substrate width.
- `l1`: [15.00, 45.00] mm; upper slot length.
- `l2`: [5.00, 15.00] mm; middle slot length.
- `l3`: [15.00, 45.00] mm; lower slot length.
- `s1`: [0.50, 1.50] mm; upper slot width.
- `s2`: [2.00, 10.00] mm; middle slot width.
- `s3`: [0.20, 1.60] mm; lower slot width.
- `f_l`: [5.00, 15.00] mm; feed-line length.

# how to read an evaluated sample and its ranking score

compact sample contract:
- one complete evaluated sample occupies one line. `+` only separates sections; it is not arithmetic addition.
- `rank:n` is the sample's position after sorting the available evaluated samples by ranking score `R` from highest to lowest. `rank:1` is the highest-ranked sample, `rank:2` is the second highest, and a smaller rank number is better. rank is comparative, not a persistent sample identifier.
- `geom={a:value,b:value,l1:value,l2:value,l3:value,s1:value,s2:value,s3:value,f_l:value}` is the evaluated antenna geometry in mm: `a`=substrate length, `b`=substrate width, `l1`=upper slot length, `l2`=middle slot length, `l3`=lower slot length, `s1`=upper slot width, `s2`=middle slot width, `s3`=lower slot width, and `f_l`=feed-line length. every value is continuous; no low/mid/high category is implied.
- `n_res` is the number of valid resonances detected from the complete measured s11 curve after the configured prominence, separation, and saddle checks. it is exactly 0, 1, or 2 and must equal the number of pairs in `res`.
- `res` contains the detected actual resonance valleys. each pair is `[resonance_frequency_ghz,s11_at_that_resonance_db]`. a more-negative second value means a deeper resonance valley. when two pairs exist they are ordered by increasing frequency.
- the valid resonance forms are `{n_res:0} + {res:[]}`, `{n_res:1} + {res:[[f1,s11_1]]}`, and `{n_res:2} + {res:[[f1,s11_1],[f2,s11_2]]}`. for `n_res:1`, the sole resonance is not automatically assigned to the low band; compare its frequency with both 2.45 ghz and 4.50 ghz. absent resonances are omitted, not represented by physical zero values, and must not be invented.
- `target_s11={2.45:s_low,4.50:s_high}` contains s11 linearly interpolated at the two fixed target frequencies. these values drive the numerical score and are distinct from resonance-valley depths in `res`. at either target, a more-negative value is better; a value at or below -15.00 db satisfies that band's matching threshold.
- `score:R` is used only for ranking. The detector stores the first and second frequency-ordered resonance slots as `r1` and `r2`; when two resonances exist, `r1` is the lower-frequency detected valley and `r2` is the higher-frequency detected valley. A missing slot is stored internally as `0.00 GHz` only so the ranking formula can be evaluated; this placeholder is not a measured or physical resonance and is omitted from `res`. The frequency quality is `q_r1=exp(-abs(r1-2.45)/0.05)`, `q_r2=exp(-abs(r2-4.50)/0.05)`, and `q_f=(q_r1+q_r2)/2`. If there is only one detected resonance, do not assume it belongs to the low band: `r1` is simply the one detected slot, `r2` is the missing-slot placeholder, and `n_res` plus `res` remain the authoritative evidence about what was detected. The target-frequency S11 quality is `q_s=0.500000*q_band(s_low)+0.500000*q_band(s_high)`, where `q_band(s)=clip((-s)/abs(-15.00),0,1)`. Here `s_low` and `s_high` are S11 values interpolated at the fixed target frequencies, not at the actual resonance minima. Finally `R=0.600000*q_f+0.400000*q_s`. R is not a success condition.
- complete example: `{rank:1} + {geom:{a:125.00,b:60.00,l1:30.00,l2:10.00,l3:30.00,s1:1.00,s2:5.00,s3:0.90,f_l:10.00}} + {n_res:2} + {res:[[2.48,-15.40],[4.47,-15.70]]} + {target_s11:{2.45:-12.80,4.50:-13.90}} + {score:0.667500}`.

# evidence policy

- use the supplied evaluated-sample summaries as the source of empirical performance evidence, and combine them with established electromagnetic design knowledge for qualitative design reasoning. Do not invent measured results.
- do not invent simulated values, predicted scores, resonance frequencies, or s11 values.
- use comparisons between the supplied geometries and measured metrics to choose plausible coordinated changes.
- every variable must remain inside its global range.
- every geometry item is a continuous numerical value in mm. 
- always read `n_res` together with the variable-length `res` list. `n_res = 0` or `n_res = 1` means that the required dual-resonance objective has not yet been satisfied.
- absent resonances are omitted from `res`. never infer a physical value for an omitted resonance.
- use lowercase variable names only: `a`, `b`, `l1`, `l2`, `l3`, `s1`, `s2`, `s3`, `f_l`.
- provide one coherent prose paragraph in `rationale` for every proposal. Explain the geometry choices and parameter adjustments by combining electromagnetic design knowledge with comparisons of the supplied top-20 ranked samples. Describe how the proposed coordinated geometry changes could plausibly affect current paths, coupling, impedance matching, and S11; treat these as design considerations rather than measured outcomes. Do not invent sample identifiers or unmeasured results, and make the paragraph readable to a human reviewer. Do not use bullets, fragments, nested JSON, or separate rationale fields.
- explain why each proposal can plausibly improve the target-frequency match based on the supplied evidence.

# top-k evaluated samples 

each following line is a ranked evaluated sample. compare aligned fields across lines; persistent identifiers and provenance are intentionally omitted:

{rank:1} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.45,-26.44],[4.53,-15.61]]} + {target_s11:{2.45:-26.44,4.50:-15.46}} + {score:0.864643}
{rank:2} + {geom:{a:115.00,b:50.00,l1:22.50,l2:14.95,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.46,-25.91],[4.52,-16.25]]} + {target_s11:{2.45:-25.43,4.50:-16.18}} + {score:0.846715}
{rank:3} + {geom:{a:115.00,b:50.00,l1:22.50,l2:14.90,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.44,-25.70],[4.52,-15.60]]} + {target_s11:{2.45:-25.70,4.50:-15.55}} + {score:0.846715}
{rank:4} + {geom:{a:115.00,b:50.00,l1:22.50,l2:14.83,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.47,-27.40],[4.53,-15.02]]} + {target_s11:{2.45:-22.70,4.50:-14.92}} + {score:0.764720}
{rank:5} + {geom:{a:115.00,b:50.00,l1:22.50,l2:14.00,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.42,-25.60],[4.35,-16.84]]} + {target_s11:{2.45:-19.46,4.50:-13.27}} + {score:0.556504}
{rank:6} + {geom:{a:113.08,b:46.27,l1:21.93,l2:14.87,l3:34.83,s1:0.82,s2:3.40,s3:0.63,f_l:11.76}} + {n_res:2} + {res:[[2.47,-19.23],[4.80,-13.16]]} + {target_s11:{2.45:-18.73,4.50:-9.53}} + {score:0.528845}
{rank:7} + {geom:{a:112.26,b:47.82,l1:22.59,l2:14.55,l3:32.20,s1:0.76,s2:3.67,s3:0.41,f_l:11.59}} + {n_res:2} + {res:[[2.71,-22.67],[4.51,-13.05]]} + {target_s11:{2.45:-6.82,4.50:-13.05}} + {score:0.512247}
{rank:8} + {geom:{a:135.00,b:80.00,l1:25.00,l2:14.00,l3:22.50,s1:1.00,s2:2.00,s3:0.90,f_l:12.00}} + {n_res:2} + {res:[[3.63,-12.88],[4.51,-14.56]]} + {target_s11:{2.45:-2.43,4.50:-14.49}} + {score:0.471169}
{rank:9} + {geom:{a:110.00,b:65.00,l1:30.00,l2:7.00,l3:17.50,s1:1.10,s2:4.00,s3:0.60,f_l:5.00}} + {n_res:2} + {res:[[2.49,-15.15],[4.31,-8.63]]} + {target_s11:{2.45:-13.06,4.50:-7.18}} + {score:0.411379}
{rank:10} + {geom:{a:140.00,b:50.00,l1:37.50,l2:10.00,l3:32.50,s1:1.30,s2:8.00,s3:1.10,f_l:14.00}} + {n_res:2} + {res:[[2.41,-15.88],[5.13,-8.58]]} + {target_s11:{2.45:-15.13,4.50:-1.38}} + {score:0.353157}
{rank:11} + {geom:{a:150.00,b:40.00,l1:40.00,l2:8.00,l3:30.00,s1:0.60,s2:3.00,s3:0.40,f_l:13.00}} + {n_res:2} + {res:[[2.50,-12.13],[4.74,-11.64]]} + {target_s11:{2.45:-11.81,4.50:-5.90}} + {score:0.348894}
{rank:12} + {geom:{a:120.00,b:55.00,l1:35.00,l2:15.00,l3:32.50,s1:1.30,s2:3.00,s3:0.30,f_l:6.00}} + {n_res:2} + {res:[[2.41,-33.53],[2.67,-37.04]]} + {target_s11:{2.45:-13.63,4.50:-1.78}} + {score:0.340312}
{rank:13} + {geom:{a:130.00,b:75.00,l1:32.50,l2:7.00,l3:37.50,s1:1.20,s2:7.00,s3:0.50,f_l:12.00}} + {n_res:2} + {res:[[2.40,-20.61],[4.70,-4.88]]} + {target_s11:{2.45:-17.41,4.50:-1.61}} + {score:0.337329}
{rank:14} + {geom:{a:105.00,b:80.00,l1:45.00,l2:13.00,l3:27.50,s1:0.90,s2:5.00,s3:0.20,f_l:5.00}} + {n_res:2} + {res:[[3.11,-10.91],[4.44,-11.97]]} + {target_s11:{2.45:-2.03,4.50:-10.55}} + {score:0.258075}
{rank:15} + {geom:{a:115.07,b:52.42,l1:23.56,l2:15.00,l3:32.87,s1:0.71,s2:4.79,s3:0.43,f_l:10.33}} + {n_res:2} + {res:[[2.68,-25.28],[4.21,-22.93]]} + {target_s11:{2.45:-7.16,4.50:-9.18}} + {score:0.221710}
{rank:16} + {geom:{a:114.09,b:51.75,l1:20.65,l2:14.56,l3:32.59,s1:0.84,s2:4.67,s3:0.39,f_l:11.98}} + {n_res:2} + {res:[[2.65,-38.26],[5.10,-27.14]]} + {target_s11:{2.45:-8.00,4.50:-6.27}} + {score:0.195723}
{rank:17} + {geom:{a:120.00,b:50.00,l1:42.50,l2:7.00,l3:35.00,s1:0.50,s2:2.00,s3:1.00,f_l:9.00}} + {n_res:2} + {res:[[2.28,-13.05],[4.21,-13.38]]} + {target_s11:{2.45:-10.02,4.50:-2.49}} + {score:0.177733}
{rank:18} + {geom:{a:105.00,b:75.00,l1:17.50,l2:10.00,l3:35.00,s1:0.90,s2:3.00,s3:1.30,f_l:8.00}} + {n_res:2} + {res:[[2.28,-18.68],[5.11,-23.66]]} + {target_s11:{2.45:-8.04,4.50:-4.51}} + {score:0.177423}
{rank:19} + {geom:{a:130.00,b:55.00,l1:30.00,l2:11.00,l3:40.00,s1:0.50,s2:2.00,s3:1.40,f_l:11.00}} + {n_res:2} + {res:[[2.65,-14.99],[5.01,-11.93]]} + {target_s11:{2.45:-8.24,4.50:-4.30}} + {score:0.172682}
{rank:20} + {geom:{a:140.00,b:60.00,l1:37.50,l2:12.00,l3:42.50,s1:1.10,s2:7.00,s3:1.50,f_l:9.00}} + {n_res:2} + {res:[[2.16,-17.46],[4.65,-6.50]]} + {target_s11:{2.45:-6.19,4.50:-4.72}} + {score:0.161305}

# required output

return only one valid json object with this exact shape:

```json
{
  "proposals": [
    {
      "variables": {
        "a": 125.00,
        "b": 60.00,
        "l1": 30.00,
        "l2": 10.00,
        "l3": 30.00,
        "s1": 1.00,
        "s2": 5.00,
        "s3": 0.90,
        "f_l": 10.00
      },
      "rationale": "one coherent human-readable english paragraph explaining why this complete design is proposed"
    }
  ]
}
```

the array length must be exactly 2. every proposal must contain all nine variables, and every numerical value must be reported to two decimal places. `rationale` must be one non-empty prose paragraph. do not include markdown fences or commentary outside the json object.

~~~

采样 LLM 的短纠错提示词（只有前次响应不合格时才会发送）：

~~~text
This is a sampling correction request in the same iteration. The previous sampling response was rejected. {issue} Correct this specific problem and return exactly {count} complete candidate designs. Using electromagnetic design knowledge together with the TOP-{top_k} evaluated samples already present in the conversation, generate exactly {count} candidate designs again. The targets remain {low_target:.2f} GHz and {high_target:.2f} GHz. Return only one JSON object with a proposals array. The array length must be exactly the requested number; every item must contain all nine legal variables, values inside their configured ranges, and a non-empty rationale. Do not repeat the long evidence section because it is already in the conversation.

{issue} is one of:
- The previous response contained fewer candidates than required ({details}). You must add the missing candidates.
- The previous response contained more candidates than required ({details}). You must remove the extra candidates.
- The previous response was not one usable JSON object with a proposals array. Return parseable JSON only.
- The response had the requested array length, but one or more candidate objects violated the schema: {details}
~~~

## 2. NDU_noNN_Two_Agent：采样 LLM 完整输入

取数运行：O_2026_10_14_0830_ds_two_candidate，最后成功的采样迭代：5。下面是当前提示词版本，样本和变量设计指导手册取自该次运行最后成功采样调用。

~~~text
# role

you are the sampling guidance module in a closed-loop dual-band antenna optimization system.

# design goal

1. Use the design information in the supplied top-20 evaluated samples, which are sorted from highest to lowest by their ranking score, and the current variable design guidebook, together with electromagnetic design knowledge. Carefully reason about how the antenna geometry parameters and their coordinated changes can affect current paths, coupling, impedance matching, and the measured S11 response. Use the guidebook's evidence-bounded interaction rules as design guidance, then propose new candidate designs that aim to satisfy the design goal.
2. The target design should have two actual resonance frequencies: one between 2.44 and 2.46 GHz, and the other between 4.49 and 4.51 GHz. At each fixed target frequency, the measured S11 must be no higher than -15.00 dB.
3. The supplied `score:R` combines actual resonance-position quality and fixed-target S11 quality. It is the score used to sort and select the supplied top-20 evaluated samples from highest to lowest; it is for ranking only and is not the success condition.
4. Generate exactly 2 legal candidate designs intended to improve resonance positions and target-frequency S11 and meet the design goal after real electromagnetic evaluation.
# legal variable ranges

every generated numerical value must use two decimal places and remain inside its corresponding closed interval:

- `a`: [100.00, 150.00] mm; substrate length.
- `b`: [40.00, 80.00] mm; substrate width.
- `l1`: [15.00, 45.00] mm; upper slot length.
- `l2`: [5.00, 15.00] mm; middle slot length.
- `l3`: [15.00, 45.00] mm; lower slot length.
- `s1`: [0.50, 1.50] mm; upper slot width.
- `s2`: [2.00, 10.00] mm; middle slot width.
- `s3`: [0.20, 1.60] mm; lower slot width.
- `f_l`: [5.00, 15.00] mm; feed-line length.

# how to read an evaluated sample and its ranking score

compact sample contract:
- one complete evaluated sample occupies one line. `+` only separates sections; it is not arithmetic addition.
- `rank:n` is the sample's position after sorting the available evaluated samples by ranking score `R` from highest to lowest. `rank:1` is the highest-ranked sample, `rank:2` is the second highest, and a smaller rank number is better. rank is comparative, not a persistent sample identifier.
- `geom={a:value,b:value,l1:value,l2:value,l3:value,s1:value,s2:value,s3:value,f_l:value}` is the evaluated antenna geometry in mm: `a`=substrate length, `b`=substrate width, `l1`=upper slot length, `l2`=middle slot length, `l3`=lower slot length, `s1`=upper slot width, `s2`=middle slot width, `s3`=lower slot width, and `f_l`=feed-line length. every value is continuous; no low/mid/high category is implied.
- `n_res` is the number of valid resonances detected from the complete measured s11 curve after the configured prominence, separation, and saddle checks. it is exactly 0, 1, or 2 and must equal the number of pairs in `res`.
- `res` contains the detected actual resonance valleys. each pair is `[resonance_frequency_ghz,s11_at_that_resonance_db]`. a more-negative second value means a deeper resonance valley. when two pairs exist they are ordered by increasing frequency.
- the valid resonance forms are `{n_res:0} + {res:[]}`, `{n_res:1} + {res:[[f1,s11_1]]}`, and `{n_res:2} + {res:[[f1,s11_1],[f2,s11_2]]}`. for `n_res:1`, the sole resonance is not automatically assigned to the low band; compare its frequency with both 2.45 ghz and 4.50 ghz. absent resonances are omitted, not represented by physical zero values, and must not be invented.
- `target_s11={2.45:s_low,4.50:s_high}` contains s11 linearly interpolated at the two fixed target frequencies. these values drive the numerical score and are distinct from resonance-valley depths in `res`. at either target, a more-negative value is better; a value at or below -15.00 db satisfies that band's matching threshold.
- `score:R` is used only for ranking. The detector stores the first and second frequency-ordered resonance slots as `r1` and `r2`; when two resonances exist, `r1` is the lower-frequency detected valley and `r2` is the higher-frequency detected valley. A missing slot is stored internally as `0.00 GHz` only so the ranking formula can be evaluated; this placeholder is not a measured or physical resonance and is omitted from `res`. The frequency quality is `q_r1=exp(-abs(r1-2.45)/0.05)`, `q_r2=exp(-abs(r2-4.50)/0.05)`, and `q_f=(q_r1+q_r2)/2`. If there is only one detected resonance, do not assume it belongs to the low band: `r1` is simply the one detected slot, `r2` is the missing-slot placeholder, and `n_res` plus `res` remain the authoritative evidence about what was detected. The target-frequency S11 quality is `q_s=0.500000*q_band(s_low)+0.500000*q_band(s_high)`, where `q_band(s)=clip((-s)/abs(-15.00),0,1)`. Here `s_low` and `s_high` are S11 values interpolated at the fixed target frequencies, not at the actual resonance minima. Finally `R=0.600000*q_f+0.400000*q_s`. R is not a success condition.
- complete example: `{rank:1} + {geom:{a:125.00,b:60.00,l1:30.00,l2:10.00,l3:30.00,s1:1.00,s2:5.00,s3:0.90,f_l:10.00}} + {n_res:2} + {res:[[2.48,-15.40],[4.47,-15.70]]} + {target_s11:{2.45:-12.80,4.50:-13.90}} + {score:0.667500}`.

# evidence policy

- use the supplied evaluated-sample summaries as the source of empirical performance evidence, combine them with the current variable design guidebook, and use established electromagnetic design knowledge for qualitative reasoning. Do not invent measured results.
- treat every learned interaction rule as a trend rather than a physical law. `confidence` is the experience llm's evidence assessment, not verified performance and not a probability calculated by code.
- do not invent simulated values, predicted scores, resonance frequencies, or s11 values.
- vary coordinated groups when a guidebook rule supports the change.
- every variable must remain inside its global range.
- every geometry item is a continuous numerical value in mm. no fixed low/mid/high partition is supplied or allowed.
- always read `n_res` together with the variable-length `res` list. `n_res = 0` or `n_res = 1` means that the required dual-resonance objective has not yet been satisfied.
- absent resonances are omitted from `res`. never infer a physical value for an omitted resonance.
- use lowercase variable names only: `a`, `b`, `l1`, `l2`, `l3`, `s1`, `s2`, `s3`, `f_l`.
- provide one coherent prose paragraph in `rationale` for every proposal. Explain the geometry choices and parameter adjustments by combining electromagnetic design knowledge with comparisons of the supplied top-20 ranked samples and the current variable design guidebook. Describe how the proposed coordinated geometry changes could plausibly affect current paths, coupling, impedance matching, and S11; treat these as design considerations rather than measured outcomes. Do not invent sample identifiers or unmeasured results, and make the paragraph readable to a human reviewer. Do not use bullets, fragments, nested JSON, or separate rationale fields.
- explain why each proposal can plausibly improve the target-frequency match based on the supplied evidence, without claiming unmeasured results.
- do not claim that a candidate satisfies the stopping condition before a new real HFSS evaluation; the code checks two resonances, both frequency tolerances, and both fixed-target S11 thresholds directly.

# top-k evaluated samples without iteration

each following line is a ranked evaluated sample. compare aligned fields across lines; persistent identifiers and provenance are intentionally omitted:

{rank:1} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.48,f_l:11.00}} + {n_res:2} + {res:[[2.45,-25.80],[4.52,-16.11]]} + {target_s11:{2.45:-25.80,4.50:-16.04}} + {score:0.901096}
{rank:2} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.45,-26.44],[4.53,-15.61]]} + {target_s11:{2.45:-26.44,4.50:-15.46}} + {score:0.864643}
{rank:3} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:34.90,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.46,-25.92],[4.53,-15.39]]} + {target_s11:{2.45:-25.11,4.50:-15.25}} + {score:0.810263}
{rank:4} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.46,f_l:11.00}} + {n_res:2} + {res:[[2.47,-26.19],[4.53,-15.24]]} + {target_s11:{2.45:-23.63,4.50:-15.12}} + {score:0.765740}
{rank:5} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:34.90,s1:0.80,s2:4.00,s3:0.46,f_l:11.00}} + {n_res:2} + {res:[[2.48,-27.97],[4.54,-15.52]]} + {target_s11:{2.45:-21.65,4.50:-15.24}} + {score:0.699442}
{rank:6} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:33.50,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.60,-29.70],[4.54,-15.38]]} + {target_s11:{2.45:-10.49,4.50:-15.11}} + {score:0.489559}
{rank:7} + {geom:{a:135.00,b:80.00,l1:25.00,l2:14.00,l3:22.50,s1:1.00,s2:2.00,s3:0.90,f_l:12.00}} + {n_res:2} + {res:[[3.63,-12.88],[4.51,-14.56]]} + {target_s11:{2.45:-2.43,4.50:-14.49}} + {score:0.471169}
{rank:8} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:32.50,s1:0.80,s2:4.00,s3:0.40,f_l:11.00}} + {n_res:2} + {res:[[2.70,-30.91],[4.54,-15.46]]} + {target_s11:{2.45:-7.27,4.50:-15.26}} + {score:0.433776}
{rank:9} + {geom:{a:110.00,b:65.00,l1:30.00,l2:7.00,l3:17.50,s1:1.10,s2:4.00,s3:0.60,f_l:5.00}} + {n_res:2} + {res:[[2.49,-15.15],[4.31,-8.63]]} + {target_s11:{2.45:-13.06,4.50:-7.18}} + {score:0.411379}
{rank:10} + {geom:{a:140.00,b:50.00,l1:37.50,l2:10.00,l3:32.50,s1:1.30,s2:8.00,s3:1.10,f_l:14.00}} + {n_res:2} + {res:[[2.41,-15.88],[5.13,-8.58]]} + {target_s11:{2.45:-15.13,4.50:-1.38}} + {score:0.353157}
{rank:11} + {geom:{a:150.00,b:40.00,l1:40.00,l2:8.00,l3:30.00,s1:0.60,s2:3.00,s3:0.40,f_l:13.00}} + {n_res:2} + {res:[[2.50,-12.13],[4.74,-11.64]]} + {target_s11:{2.45:-11.81,4.50:-5.90}} + {score:0.348894}
{rank:12} + {geom:{a:120.00,b:55.00,l1:35.00,l2:15.00,l3:32.50,s1:1.30,s2:3.00,s3:0.30,f_l:6.00}} + {n_res:2} + {res:[[2.41,-33.53],[2.67,-37.04]]} + {target_s11:{2.45:-13.63,4.50:-1.78}} + {score:0.340312}
{rank:13} + {geom:{a:130.00,b:75.00,l1:32.50,l2:7.00,l3:37.50,s1:1.20,s2:7.00,s3:0.50,f_l:12.00}} + {n_res:2} + {res:[[2.40,-20.61],[4.70,-4.88]]} + {target_s11:{2.45:-17.41,4.50:-1.61}} + {score:0.337329}
{rank:14} + {geom:{a:105.00,b:80.00,l1:45.00,l2:13.00,l3:27.50,s1:0.90,s2:5.00,s3:0.20,f_l:5.00}} + {n_res:2} + {res:[[3.11,-10.91],[4.44,-11.97]]} + {target_s11:{2.45:-2.03,4.50:-10.55}} + {score:0.258075}
{rank:15} + {geom:{a:120.00,b:50.00,l1:42.50,l2:7.00,l3:35.00,s1:0.50,s2:2.00,s3:1.00,f_l:9.00}} + {n_res:2} + {res:[[2.28,-13.05],[4.21,-13.38]]} + {target_s11:{2.45:-10.02,4.50:-2.49}} + {score:0.177733}
{rank:16} + {geom:{a:105.00,b:75.00,l1:17.50,l2:10.00,l3:35.00,s1:0.90,s2:3.00,s3:1.30,f_l:8.00}} + {n_res:2} + {res:[[2.28,-18.68],[5.11,-23.66]]} + {target_s11:{2.45:-8.04,4.50:-4.51}} + {score:0.177423}
{rank:17} + {geom:{a:130.00,b:55.00,l1:30.00,l2:11.00,l3:40.00,s1:0.50,s2:2.00,s3:1.40,f_l:11.00}} + {n_res:2} + {res:[[2.65,-14.99],[5.01,-11.93]]} + {target_s11:{2.45:-8.24,4.50:-4.30}} + {score:0.172682}
{rank:18} + {geom:{a:140.00,b:60.00,l1:37.50,l2:12.00,l3:42.50,s1:1.10,s2:7.00,s3:1.50,f_l:9.00}} + {n_res:2} + {res:[[2.16,-17.46],[4.65,-6.50]]} + {target_s11:{2.45:-6.19,4.50:-4.72}} + {score:0.161305}
{rank:19} + {geom:{a:119.17,b:47.45,l1:20.10,l2:14.93,l3:37.60,s1:0.81,s2:4.32,s3:0.50,f_l:11.80}} + {n_res:2} + {res:[[2.23,-22.02],[5.34,-19.85]]} + {target_s11:{2.45:-5.26,4.50:-5.49}} + {score:0.146989}
{rank:20} + {geom:{a:110.00,b:50.00,l1:27.50,l2:9.00,l3:40.00,s1:0.60,s2:4.00,s3:0.70,f_l:6.00}} + {n_res:2} + {res:[[2.79,-29.08],[4.71,-10.97]]} + {target_s11:{2.45:-4.97,4.50:-5.53}} + {score:0.144783}

# how to interpret a variable-interaction rule

example rule:
rule = r-example
domain = {l1:[26.40,34.20], s1:[0.55,0.88]}
var_change = {l1:u, s1:d}
effect = {low_resonance_point:h, s11_at_2_45:d, high_resonance_point:l, s11_at_4_50:s}
confidence = 0.68

example interpretation:
- the rule is an evidence-supported trend, not a universal physical law.
- the rule applies only when the starting design has `l1` in [26.40,34.20] mm and `s1` in [0.55,0.88] mm.
- `l1:u` and `s1:d` form one coordinated action: increase `l1` while decreasing `s1`.
- `low_resonance_point:h` means that the actual low-band resonance frequency is expected to move higher.
- `s11_at_2_45:d` means that s11 at 2.45 ghz is expected to become deeper, meaning more negative.
- `high_resonance_point:l` means that the actual high-band resonance frequency is expected to move lower.
- `s11_at_4_50:s` means that s11 at 4.50 ghz is expected to become shallower, meaning less negative.
- `k` may be used for either metric type only when the supplied evidence indicates no clear meaningful change.
- `confidence = 0.68` is the experience llm's direct assessment of the supplied evidence. code validates the number but does not calculate or upgrade it.

# current variable design guidebook

variable design guidebook format:
- this guidebook is a compact snapshot of evidence-bounded trends returned by the experience llm and validated by code.
- each active rule is a coordinated action, not independent advice for each variable.
- `rule=r-....` is an internal label; use it in `rules_used` only when the candidate deliberately applies that rule.
- `domain=global` means the trend has no narrower supplied starting range. An object such as `{l1:[26.40,34.20],s1:[0.55,0.88]}` means the rule applies only when every listed starting variable lies in its inclusive range; omitted variables are unrestricted by that rule.
- `var_change` uses `u`=increase, `d`=decrease, and `k`=keep approximately unchanged. The complete mapping is one coordinated geometry change.
- `effect` always contains four metrics. For resonance positions, `l`=frequency moves lower, `h`=frequency moves higher, and `k`=no clear movement. For fixed-target S11, `d`=deeper/more negative, `s`=shallower/less negative, and `k`=no clear depth change.
- an omitted resonance is unknown evidence, never evidence for `k`; do not apply a rule that claims an unavailable resonance effect.
- `confidence` is the experience llm's evidence assessment in [0,1); it is not a probability calculated by code. `note` is the human-readable explanation of the coordinated trend and its evidence limits.
- rules are hypotheses, not universal physical laws. Use them to guide plausible candidates, but keep every generated variable inside its global legal range and do not claim unmeasured outcomes.

complete rule example:
rule=r-example
domain={l1:[26.40,34.20],s1:[0.55,0.88]}
var_change={l1:u,s1:d}
effect={low_resonance_point:h,s11_at_2_45:d,high_resonance_point:l,s11_at_4_50:s}
confidence=0.68
note=in this local starting domain, increasing l1 while decreasing s1 tends to move the low resonance higher, deepen s11 at 2.45 ghz, move the high resonance lower, and make s11 at 4.50 ghz shallower; the trend remains evidence-limited.

how to use the example:
- apply it only to a starting geometry inside both listed domain ranges.
- change l1 upward and s1 downward together; do not treat them as unrelated one-variable rules.
- expect the stated directions as a hypothesis, not a guaranteed result.
- cite `r-example` in `rules_used` only if the proposal actually follows this coordinated action.

# runtime variable design guidebook

this file is the direct, code-validated rule snapshot returned by the experience llm. confidence is the experience llm's evidence assessment, not a probability calculated or upgraded by code. every rule is a trend, not a universal physical law.

## active rules



rule=r-0004-01
domain={a:[115.00,115.00],b:[50.00,50.00],l1:[22.50,22.50],l2:[15.00,15.00],l3:[32.50,35.00],s1:[0.80,0.80],s2:[4.00,4.00],s3:[0.40,0.50],f_l:[11.00,11.00]}
var_change={l3:u}
effect={low_resonance_point:l,s11_at_2_45:d,high_resonance_point:k,s11_at_4_50:k}
confidence=0.72
note=Within the top-ranked a=115,b=50,l1=22.5,l2=15,s1=0.8,s2=4,f_l=11 family, increasing l3 from 32.5 to 35 lowers the first resonance from about 2.70 to 2.45 and deepens S11 at 2.45 substantially, while the second resonance stays near 4.52-4.54 GHz and S11 at 4.50 remains near -15.1 to -16.0 dB. Supported by six nearby samples in this narrow domain.



rule=r-0004-02
domain={a:[115.00,115.00],b:[50.00,50.00],l1:[22.50,22.50],l2:[15.00,15.00],l3:[34.90,35.00],s1:[0.80,0.80],s2:[4.00,4.00],s3:[0.46,0.50],f_l:[11.00,11.00]}
var_change={s3:u}
effect={low_resonance_point:l,s11_at_2_45:d,high_resonance_point:k,s11_at_4_50:k}
confidence=0.42
note=In the near-optimal a=115,b=50,l1=22.5,l2=15,l3~35,s1=0.8,s2=4,f_l=11 family, increasing s3 from about 0.46 to 0.50 lowers the first resonance from roughly 2.47 to 2.45 and deepens S11 at 2.45 by a few dB, while the high resonance and S11 at 4.50 show no clear meaningful change. Based on three to four nearby samples; limited to fine tuning.



rule=r-0004-03
domain={a:[145.00,145.00],b:[45.00,45.00],l1:[15.00,15.00],l2:[6.00,6.00],l3:[22.50,25.00],s1:[1.40,1.50],s2:[6.00,7.00],s3:[0.70,1.00],f_l:[13.00,15.00]}
var_change={l3:u,s1:d,s2:u,s3:d,f_l:d}
effect={low_resonance_point:l,s11_at_2_45:d,high_resonance_point:l,s11_at_4_50:d}
confidence=0.32
note=In the a=145,b=45,l1=15,l2=6 family, increasing l3 from 22.5 to 25 while decreasing s1, s3, and f_l and increasing s2 lowered both observed resonances and deepened S11 at 2.45 and 4.50. Only two samples support this and the joint changes are strongly confounded.



rule=r-0004-04
domain={a:[110.00,120.00],b:[50.00,65.00],l1:[30.00,42.50],l2:[7.00,7.00],l3:[17.50,35.00],s1:[0.50,1.10],s2:[2.00,4.00],s3:[0.50,1.00],f_l:[5.00,9.00]}
var_change={a:d,b:u,l1:d,l3:d,s1:u,s2:u,s3:d,f_l:d}
effect={low_resonance_point:h,s11_at_2_45:d,high_resonance_point:h,s11_at_4_50:d}
confidence=0.27
note=In the l2=7 starting region represented by two samples, decreasing a, l1, l3, s3, and f_l while increasing b, s1, and s2 moved both resonances higher and deepened S11 at 2.45 and 4.50. The evidence is a single nearby comparison with simultaneous changes, so confidence is low.



rule=r-0004-05
domain={a:[130.00,140.00],b:[50.00,75.00],l1:[32.50,37.50],l2:[7.00,10.00],l3:[32.50,37.50],s1:[1.20,1.30],s2:[7.00,8.00],s3:[0.50,1.10],f_l:[12.00,14.00]}
var_change={a:d,b:u,l1:d,l2:d,l3:u,s1:d,s2:d,s3:d,f_l:d}
effect={low_resonance_point:k,s11_at_2_45:d,high_resonance_point:l,s11_at_4_50:k}
confidence=0.25
note=In the a=130-140 starting region where the second resonance is too high, decreasing a, l1, l2, s1, s2, s3, and f_l while increasing b and l3 moved the second resonance lower from about 5.13 to 4.70 GHz and deepened S11 at 2.45, while the first resonance and S11 at 4.50 showed no meaningful movement. Supported by one nearby comparison with many simultaneous changes.


# required output

return only one valid json object with this exact shape:

```json
{
  "proposals": [
    {
      "variables": {
        "a": 125.00,
        "b": 60.00,
        "l1": 30.00,
        "l2": 10.00,
        "l3": 30.00,
        "s1": 1.00,
        "s2": 5.00,
        "s3": 0.90,
        "f_l": 10.00
      },
      "rationale": "one coherent human-readable english paragraph explaining why this complete design is proposed",
      "rules_used": ["r-001"]
    }
  ]
}
```

the array length must be exactly 2. every proposal must contain all nine variables, and every numerical value must be reported to two decimal places. `rationale` must be one non-empty prose paragraph. do not include markdown fences or commentary outside the json object.

# current optimization context

{
  "task": "generate candidate designs for a dual-band dipole antenna",
  "proposal_count": 2,
  "top_sample_count": 20
}

~~~

采样 LLM 的短纠错提示词（只有前次响应不合格时才会发送）：

~~~text
This is a sampling correction request in the same iteration. The previous sampling response was rejected. {issue} Correct this specific problem and return exactly {count} complete candidate designs. Using electromagnetic design knowledge together with the TOP-{top_k} evaluated samples and the current variable design guidebook already present in the conversation, generate exactly {count} candidate designs again. The targets remain {low_target:.2f} GHz and {high_target:.2f} GHz. Return only one JSON object with a proposals array. The array length must be exactly the requested number; every item must contain all nine legal variables, values inside their configured ranges, a non-empty rationale, and rules_used. Do not repeat the long evidence section because it is already in the conversation.

{issue} is one of the four correction issue strings listed in the project code: too few candidates, too many candidates, unusable JSON, or candidate schema violation.
~~~

## 3. NDU_noNN_Two_Agent：经验总结 LLM 完整输入

取数运行：O_2026_10_14_0830_ds_two_candidate，最后成功的经验总结迭代：5。经验提示词模板没有被本次采样提示词修改所影响；以下是该次运行实际发送的完整 user prompt，包含 TOP-Q 样本和 previous_active_rules。

~~~text
# role

you are the experience summary module in a closed-loop dual-band antenna optimization system.

# objective

infer the latest complete variable design guidebook from up to the configured score-ranked TOP-40 evaluated samples and the previous active guidebook rules (use all available samples when fewer than 40 exist). use the numerical geometry, actual resonance metrics, target-frequency s11 values, ranking scores, and existing rules as evidence. return the complete next guidebook snapshot: retain supported rules, revise rules when the evidence supports a narrower domain or different direction, remove a rule only when the relevant evidence shows it is unsupported, and add new rules only when the evidence supports them. sample rows contain only their rank for comparison; provenance and persistent identifiers are intentionally withheld. The ranking score combines actual resonance-position quality with target-frequency S11 quality, while the physical stopping conditions are stated separately below.

rules are empirical design-guidance trends, not hard constraints or universal laws. an individual sample may violate a rule; a rule is useful when the trend is supported by most relevant, comparable samples in its domain, even if it does not fit every sample. judge a rule by the pattern across the evidence, not by demanding universal compliance.

for this task, a design-guidance rule is a majority-supported empirical pattern between a coordinated geometry change and observed antenna effects within a stated starting-design domain. "most samples support the rule" means the supporting comparable observations are the majority of relevant observations that can actually test that rule; isolated exceptions do not invalidate it. these rules guide future candidate design and are not per-sample pass/fail requirements. only the separately stated code-controlled stopping conditions are hard requirements for declaring optimization success.

return the complete rule snapshot that should be used by the sampling guidance module in the next iteration. review the previous active rules, retain or revise only those still justified, remove unsupported rules, and add new rules only when the supplied evidence warrants them. return at most 8 rules. code validates and immediately publishes this snapshot; it does not maintain a separate evidence ledger or automatically change confidence.

# legal variable ranges

interpret every design using these configured closed intervals:

- `a`: [100.00, 150.00] mm; substrate length.
- `b`: [40.00, 80.00] mm; substrate width.
- `l1`: [15.00, 45.00] mm; upper slot length.
- `l2`: [5.00, 15.00] mm; middle slot length.
- `l3`: [15.00, 45.00] mm; lower slot length.
- `s1`: [0.50, 1.50] mm; upper slot width.
- `s2`: [2.00, 10.00] mm; middle slot width.
- `s3`: [0.20, 1.60] mm; lower slot width.
- `f_l`: [5.00, 15.00] mm; feed-line length.

# ranking score and physical stopping conditions

the code computes a ranking-only score R; it does not decide success:
q_r1 = exp(-abs(r1 - 2.45) / 0.05)
q_r2 = exp(-abs(r2 - 4.50) / 0.05)
q_f = (q_r1 + q_r2) / 2
q_band(s) = clip((-s) / abs(-15.00), 0, 1)
q_s = 0.500000 * q_band(s11_at_2_45) + 0.500000 * q_band(s11_at_4_50)
R = 0.600000 * q_f + 0.400000 * q_s
Here r1 and r2 are the first and second frequency-ordered detector slots. When a slot is missing, the code stores 0.00 GHz internally for score calculation; this placeholder is not a real resonance and is omitted from the displayed res list. With one detected resonance, do not assume it belongs to the low band: n_res and res identify the actual evidence, while r1/r2 are only the stored score slots. The values s11_at_2_45 and s11_at_4_50 are S11 interpolated at the fixed target frequencies, not at resonance minima. R is used only for ranking and TOP-40 selection; success is checked directly by the physical conditions below.

# code-controlled stopping condition

only the code decides whether optimization stops after a real hfss evaluation. it stops only when the detector finds two independent resonances, the lower resonance is within 0.01 ghz of 2.45 ghz, the higher resonance is within 0.01 ghz of 4.50 ghz, s11 at the fixed 2.45 ghz target is at most -15.00 db, and s11 at the fixed 4.50 ghz target is at most -15.00 db. R is only a ranking score and is not a success condition. the llm must never claim that a proposed candidate already satisfies the stopping condition.

# how to read an evaluated sample

compact sample contract:
- one complete evaluated sample occupies one line. `+` only separates sections; it is not arithmetic addition.
- `rank:n` is the sample's position after sorting the available evaluated samples by ranking score `R` from highest to lowest. `rank:1` is the highest-ranked sample, `rank:2` is the second highest, and a smaller rank number is better. rank is comparative, not a persistent sample identifier.
- `geom={a:value,b:value,l1:value,l2:value,l3:value,s1:value,s2:value,s3:value,f_l:value}` is the evaluated antenna geometry in mm: `a`=substrate length, `b`=substrate width, `l1`=upper slot length, `l2`=middle slot length, `l3`=lower slot length, `s1`=upper slot width, `s2`=middle slot width, `s3`=lower slot width, and `f_l`=feed-line length. every value is continuous; no low/mid/high category is implied.
- `n_res` is the number of valid resonances detected from the complete measured s11 curve after the configured prominence, separation, and saddle checks. it is exactly 0, 1, or 2 and must equal the number of pairs in `res`.
- `res` contains the detected actual resonance valleys. each pair is `[resonance_frequency_ghz,s11_at_that_resonance_db]`. a more-negative second value means a deeper resonance valley. when two pairs exist they are ordered by increasing frequency.
- the valid resonance forms are `{n_res:0} + {res:[]}`, `{n_res:1} + {res:[[f1,s11_1]]}`, and `{n_res:2} + {res:[[f1,s11_1],[f2,s11_2]]}`. for `n_res:1`, the sole resonance is not automatically assigned to the low band; compare its frequency with both 2.45 ghz and 4.50 ghz. absent resonances are omitted, not represented by physical zero values, and must not be invented.
- `target_s11={2.45:s_low,4.50:s_high}` contains s11 linearly interpolated at the two fixed target frequencies. these values drive the numerical score and are distinct from resonance-valley depths in `res`. at either target, a more-negative value is better; a value at or below -15.00 db satisfies that band's matching threshold.
- `score:R` is used only for ranking. The detector stores the first and second frequency-ordered resonance slots as `r1` and `r2`; when two resonances exist, `r1` is the lower-frequency detected valley and `r2` is the higher-frequency detected valley. A missing slot is stored internally as `0.00 GHz` only so the ranking formula can be evaluated; this placeholder is not a measured or physical resonance and is omitted from `res`. The frequency quality is `q_r1=exp(-abs(r1-2.45)/0.05)`, `q_r2=exp(-abs(r2-4.50)/0.05)`, and `q_f=(q_r1+q_r2)/2`. If there is only one detected resonance, do not assume it belongs to the low band: `r1` is simply the one detected slot, `r2` is the missing-slot placeholder, and `n_res` plus `res` remain the authoritative evidence about what was detected. The target-frequency S11 quality is `q_s=0.500000*q_band(s_low)+0.500000*q_band(s_high)`, where `q_band(s)=clip((-s)/abs(-15.00),0,1)`. Here `s_low` and `s_high` are S11 values interpolated at the fixed target frequencies, not at the actual resonance minima. Finally `R=0.600000*q_f+0.400000*q_s`. R is not a success condition.
- complete example: `{rank:1} + {geom:{a:125.00,b:60.00,l1:30.00,l2:10.00,l3:30.00,s1:1.00,s2:5.00,s3:0.90,f_l:10.00}} + {n_res:2} + {res:[[2.48,-15.40],[4.47,-15.70]]} + {target_s11:{2.45:-12.80,4.50:-13.90}} + {score:0.667500}`.

# experience rule format

experience guidebook rule contract:
 - the response is the complete latest guidebook snapshot, not an append-only list. review the previous guidebook rules against the supplied score-ranked TOP-40 selection (up to this many evaluated samples): retain a rule when most relevant, comparable samples in its domain support it, revise it when the evidence supports a narrower or different statement, remove it only when most relevant, comparable samples contradict it, and add a new rule only when evidence supports it.
- `summary` is a short evidence-bounded assessment of the complete returned snapshot.
- every item in `rules` is one coordinated qualitative trend. the experience llm outputs `domain`, `var_change`, `effect`, `confidence`, and `note`; code later assigns the internal `rule` id, provenance `source`, and `created_iteration`.
- `domain` describes the starting-geometry region where the trend may apply. use `global` only when the supplied evidence supports no narrower domain. otherwise use one or more lowercase variables with inclusive continuous bounds, for example `{"l1":{"min":26.40,"max":34.20},"s1":{"min":0.55,"max":0.88}}`. every bound must stay inside the legal range. variables omitted from `domain` are unrestricted by that rule.
- `var_change` is one coordinated proposed change, not a set of independent causal claims. each included variable uses `u`=increase, `d`=decrease, or `k`=keep approximately unchanged. include only variables needed to state the supported interaction.
- `effect` must contain exactly four fields. `low_resonance_point` and `high_resonance_point` use `l`=the numerical resonance frequency moves lower, `h`=it moves higher, or `k`=no clear meaningful movement. these symbols describe direction, not whether the move is closer to the target.
- `s11_at_2_45` and `s11_at_4_50` describe s11 at the fixed targets 2.45 ghz and 4.50 ghz. they use `d`=deeper/more negative, `s`=shallower/less negative, or `k`=no clear meaningful depth change. a missing resonance is unknown evidence and must never be translated into `k`.
- `confidence` is the llm's direct evidence-strength assessment from 0.00 through 0.99, not a probability calculated by code. use a low value for sparse, distant, or confounded comparisons and a higher value only for repeated, nearby, consistent real-HFSS comparisons. never use 1.00.
- `note` is a concise human-readable statement of the coordinated change, all four expected effects, the supported domain, and important evidence limitations. do not claim a universal physical law or certainty.
- complete example: `domain={l1:{min:26.40,max:34.20},s1:{min:0.55,max:0.88}}`; `var_change={l1:u,s1:d}`; `effect={low_resonance_point:h,s11_at_2_45:d,high_resonance_point:l,s11_at_4_50:s}`; `confidence=0.68`. this means that, only for starting designs in the stated domain, increasing `l1` while decreasing `s1` is hypothesized to move the low resonance to a numerically higher frequency, deepen s11 at 2.45 ghz, move the high resonance to a numerically lower frequency, and make s11 at 4.50 ghz shallower.

# critical reasoning constraints

- ranking or correlation alone does not prove causality.
- when evidence is weak, return fewer rules or an empty rule list.
- assess each rule using relevant, comparable samples within its domain and similar joint-change context; do not treat unrelated samples as votes for or against it.
- do not reject or remove a previous rule because one or a few samples are exceptions. retain it when most relevant, comparable samples support its trend; revise or remove it for contradictory evidence only when most of those samples fail to follow the trend. if the evidence is sparse or has no clear majority, do not claim the rule has been disproven; keep it cautiously or narrow its domain when supported, and express the uncertainty in confidence and note.
- favor coordinated interaction rules involving several variables, ideally three or more when the evidence supports them, instead of making the guidebook consist only of single-variable or two-variable trends. include only variables whose joint changes are supported by the samples; never add variables merely to make a rule more complex.
- prefer coordinated changes supported by comparisons among nearby samples.
- use only these lowercase variable names: `a`, `b`, `l1`, `l2`, `l3`, `s1`, `s2`, `s3`, `f_l`.
- `domain` is either `global` or an object that gives a continuous closed starting range for each relevant variable as `{"min":value,"max":value}`.
- domain boundaries must remain within the legal variable ranges and must be supported by the supplied continuous-valued evidence. do not recreate fixed low/mid/high partitions.
- `u`, `d`, and `k` mean increase, decrease, and keep approximately unchanged.
- for `low_resonance_point` and `high_resonance_point`, use only `l`, `h`, or `k`: `l` means the numerical resonance frequency moves lower, `h` means it moves higher, and `k` means no clear meaningful frequency movement.
- for `s11_at_2_45` and `s11_at_4_50`, use only `d`, `s`, or `k`: `d` means the fixed-target s11 becomes deeper/more negative, `s` means it becomes shallower/less negative, and `k` means no clear meaningful depth change.
- every rule must contain all four required effect metrics.
- missing resonance cannot be interpreted as `k`.
- confidence must be between 0 and 1; never use 1.00. this is your direct, evidence-bounded assessment, not a code-computed probability. use low values when comparisons are sparse or confounded; reserve high values for repeated, consistent, nearby real-hfss comparisons.
- returned rules are hypotheses. code assigns internal identifiers after the response; code does not calculate, upgrade, or downgrade confidence.
- infer trends only from the numerical geometry and measured metrics in the supplied score-ranked TOP-40 sample lines; fewer may be present when fewer evaluations are available.
- use `previous_active_rules` to determine whether the evidence suggests a new rule, supports an existing direction, or conflicts with an existing direction.

# TOP-40 evaluated samples

each following line is one score-ranked evaluated sample from the configured TOP-40 selection (or all available samples if fewer exist). persistent identifiers and provenance are intentionally omitted:

{rank:1} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.48,f_l:11.00}} + {n_res:2} + {res:[[2.45,-25.80],[4.52,-16.11]]} + {target_s11:{2.45:-25.80,4.50:-16.04}} + {score:0.901096}
{rank:2} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.45,-26.44],[4.53,-15.61]]} + {target_s11:{2.45:-26.44,4.50:-15.46}} + {score:0.864643}
{rank:3} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:34.90,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.46,-25.92],[4.53,-15.39]]} + {target_s11:{2.45:-25.11,4.50:-15.25}} + {score:0.810263}
{rank:4} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.49,f_l:11.00}} + {n_res:2} + {res:[[2.46,-26.41],[4.54,-14.95]]} + {target_s11:{2.45:-26.04,4.50:-14.76}} + {score:0.777271}
{rank:5} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:35.00,s1:0.80,s2:4.00,s3:0.46,f_l:11.00}} + {n_res:2} + {res:[[2.47,-26.19],[4.53,-15.24]]} + {target_s11:{2.45:-23.63,4.50:-15.12}} + {score:0.765740}
{rank:6} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:34.90,s1:0.80,s2:4.00,s3:0.46,f_l:11.00}} + {n_res:2} + {res:[[2.48,-27.97],[4.54,-15.52]]} + {target_s11:{2.45:-21.65,4.50:-15.24}} + {score:0.699442}
{rank:7} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:33.50,s1:0.80,s2:4.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.60,-29.70],[4.54,-15.38]]} + {target_s11:{2.45:-10.49,4.50:-15.11}} + {score:0.489559}
{rank:8} + {geom:{a:135.00,b:80.00,l1:25.00,l2:14.00,l3:22.50,s1:1.00,s2:2.00,s3:0.90,f_l:12.00}} + {n_res:2} + {res:[[3.63,-12.88],[4.51,-14.56]]} + {target_s11:{2.45:-2.43,4.50:-14.49}} + {score:0.471169}
{rank:9} + {geom:{a:117.62,b:46.91,l1:21.29,l2:14.23,l3:35.09,s1:0.71,s2:3.21,s3:0.50,f_l:10.28}} + {n_res:2} + {res:[[2.41,-17.85],[4.75,-11.58]]} + {target_s11:{2.45:-16.47,4.50:-9.09}} + {score:0.458046}
{rank:10} + {geom:{a:115.00,b:50.00,l1:22.50,l2:15.00,l3:32.50,s1:0.80,s2:4.00,s3:0.40,f_l:11.00}} + {n_res:2} + {res:[[2.70,-30.91],[4.54,-15.46]]} + {target_s11:{2.45:-7.27,4.50:-15.26}} + {score:0.433776}
{rank:11} + {geom:{a:110.00,b:65.00,l1:30.00,l2:7.00,l3:17.50,s1:1.10,s2:4.00,s3:0.60,f_l:5.00}} + {n_res:2} + {res:[[2.49,-15.15],[4.31,-8.63]]} + {target_s11:{2.45:-13.06,4.50:-7.18}} + {score:0.411379}
{rank:12} + {geom:{a:140.00,b:50.00,l1:37.50,l2:10.00,l3:32.50,s1:1.30,s2:8.00,s3:1.10,f_l:14.00}} + {n_res:2} + {res:[[2.41,-15.88],[5.13,-8.58]]} + {target_s11:{2.45:-15.13,4.50:-1.38}} + {score:0.353157}
{rank:13} + {geom:{a:150.00,b:40.00,l1:40.00,l2:8.00,l3:30.00,s1:0.60,s2:3.00,s3:0.40,f_l:13.00}} + {n_res:2} + {res:[[2.50,-12.13],[4.74,-11.64]]} + {target_s11:{2.45:-11.81,4.50:-5.90}} + {score:0.348894}
{rank:14} + {geom:{a:120.00,b:55.00,l1:35.00,l2:15.00,l3:32.50,s1:1.30,s2:3.00,s3:0.30,f_l:6.00}} + {n_res:2} + {res:[[2.41,-33.53],[2.67,-37.04]]} + {target_s11:{2.45:-13.63,4.50:-1.78}} + {score:0.340312}
{rank:15} + {geom:{a:130.00,b:75.00,l1:32.50,l2:7.00,l3:37.50,s1:1.20,s2:7.00,s3:0.50,f_l:12.00}} + {n_res:2} + {res:[[2.40,-20.61],[4.70,-4.88]]} + {target_s11:{2.45:-17.41,4.50:-1.61}} + {score:0.337329}
{rank:16} + {geom:{a:105.00,b:80.00,l1:45.00,l2:13.00,l3:27.50,s1:0.90,s2:5.00,s3:0.20,f_l:5.00}} + {n_res:2} + {res:[[3.11,-10.91],[4.44,-11.97]]} + {target_s11:{2.45:-2.03,4.50:-10.55}} + {score:0.258075}
{rank:17} + {geom:{a:120.00,b:50.00,l1:42.50,l2:7.00,l3:35.00,s1:0.50,s2:2.00,s3:1.00,f_l:9.00}} + {n_res:2} + {res:[[2.28,-13.05],[4.21,-13.38]]} + {target_s11:{2.45:-10.02,4.50:-2.49}} + {score:0.177733}
{rank:18} + {geom:{a:105.00,b:75.00,l1:17.50,l2:10.00,l3:35.00,s1:0.90,s2:3.00,s3:1.30,f_l:8.00}} + {n_res:2} + {res:[[2.28,-18.68],[5.11,-23.66]]} + {target_s11:{2.45:-8.04,4.50:-4.51}} + {score:0.177423}
{rank:19} + {geom:{a:130.00,b:55.00,l1:30.00,l2:11.00,l3:40.00,s1:0.50,s2:2.00,s3:1.40,f_l:11.00}} + {n_res:2} + {res:[[2.65,-14.99],[5.01,-11.93]]} + {target_s11:{2.45:-8.24,4.50:-4.30}} + {score:0.172682}
{rank:20} + {geom:{a:140.00,b:60.00,l1:37.50,l2:12.00,l3:42.50,s1:1.10,s2:7.00,s3:1.50,f_l:9.00}} + {n_res:2} + {res:[[2.16,-17.46],[4.65,-6.50]]} + {target_s11:{2.45:-6.19,4.50:-4.72}} + {score:0.161305}
{rank:21} + {geom:{a:119.17,b:47.45,l1:20.10,l2:14.93,l3:37.60,s1:0.81,s2:4.32,s3:0.50,f_l:11.80}} + {n_res:2} + {res:[[2.23,-22.02],[5.34,-19.85]]} + {target_s11:{2.45:-5.26,4.50:-5.49}} + {score:0.146989}
{rank:22} + {geom:{a:110.00,b:50.00,l1:27.50,l2:9.00,l3:40.00,s1:0.60,s2:4.00,s3:0.70,f_l:6.00}} + {n_res:2} + {res:[[2.79,-29.08],[4.71,-10.97]]} + {target_s11:{2.45:-4.97,4.50:-5.53}} + {score:0.144783}
{rank:23} + {geom:{a:110.00,b:70.00,l1:35.00,l2:10.00,l3:30.00,s1:1.00,s2:4.00,s3:0.60,f_l:7.00}} + {n_res:2} + {res:[[2.03,-26.67],[2.68,-31.65]]} + {target_s11:{2.45:-9.65,4.50:-1.09}} + {score:0.143198}
{rank:24} + {geom:{a:120.00,b:75.00,l1:30.00,l2:10.00,l3:37.50,s1:1.00,s2:5.00,s3:0.50,f_l:11.00}} + {n_res:2} + {res:[[2.67,-19.98],[5.16,-15.54]]} + {target_s11:{2.45:-7.88,4.50:-1.27}} + {score:0.125781}
{rank:25} + {geom:{a:145.00,b:45.00,l1:15.00,l2:6.00,l3:25.00,s1:1.40,s2:7.00,s3:0.70,f_l:13.00}} + {n_res:2} + {res:[[2.70,-11.00],[5.06,-19.35]]} + {target_s11:{2.45:-3.02,4.50:-6.22}} + {score:0.125310}
{rank:26} + {geom:{a:150.00,b:45.00,l1:27.50,l2:12.00,l3:20.00,s1:0.80,s2:5.00,s3:0.40,f_l:13.00}} + {n_res:2} + {res:[[3.03,-30.02],[4.94,-20.64]]} + {target_s11:{2.45:-3.49,4.50:-5.65}} + {score:0.121874}
{rank:27} + {geom:{a:130.00,b:60.00,l1:22.50,l2:5.00,l3:37.50,s1:0.70,s2:5.00,s3:0.20,f_l:11.00}} + {n_res:2} + {res:[[3.12,-14.12],[4.68,-18.21]]} + {target_s11:{2.45:-2.00,4.50:-6.20}} + {score:0.117469}
{rank:28} + {geom:{a:100.00,b:70.00,l1:32.50,l2:15.00,l3:40.00,s1:0.90,s2:8.00,s3:0.20,f_l:15.00}} + {n_res:2} + {res:[[2.68,-28.46],[5.31,-6.37]]} + {target_s11:{2.45:-6.27,4.50:-1.25}} + {score:0.103270}
{rank:29} + {geom:{a:135.00,b:55.00,l1:35.00,l2:8.00,l3:27.50,s1:1.40,s2:7.00,s3:0.80,f_l:12.00}} + {n_res:2} + {res:[[2.76,-15.63],[5.35,-16.17]]} + {target_s11:{2.45:-6.22,4.50:-1.21}} + {score:0.099574}
{rank:30} + {geom:{a:100.00,b:60.00,l1:27.50,l2:8.00,l3:15.00,s1:1.00,s2:6.00,s3:1.60,f_l:10.00}} + {n_res:2} + {res:[[2.67,-21.73],[5.42,-6.23]]} + {target_s11:{2.45:-4.95,4.50:-2.21}} + {score:0.099155}
{rank:31} + {geom:{a:145.00,b:45.00,l1:15.00,l2:6.00,l3:22.50,s1:1.50,s2:6.00,s3:1.00,f_l:15.00}} + {n_res:2} + {res:[[3.12,-9.45],[5.18,-12.20]]} + {target_s11:{2.45:-1.44,4.50:-4.92}} + {score:0.084897}
{rank:32} + {geom:{a:105.00,b:80.00,l1:17.50,l2:10.00,l3:45.00,s1:0.60,s2:3.00,s3:0.50,f_l:9.00}} + {n_res:2} + {res:[[4.23,-17.48],[5.07,-12.80]]} + {target_s11:{2.45:-0.83,4.50:-5.23}} + {score:0.080859}
{rank:33} + {geom:{a:115.00,b:55.00,l1:20.00,l2:11.00,l3:17.50,s1:1.10,s2:9.00,s3:0.70,f_l:8.00}} + {n_res:2} + {res:[[4.21,-13.07],[5.77,-4.92]]} + {target_s11:{2.45:-1.01,4.50:-4.31}} + {score:0.070944}
{rank:34} + {geom:{a:115.00,b:40.00,l1:17.50,l2:5.00,l3:37.50,s1:1.50,s2:2.00,s3:1.00,f_l:10.00}} + {n_res:2} + {res:[[3.84,-17.56],[5.24,-9.89]]} + {target_s11:{2.45:-0.73,4.50:-3.63}} + {score:0.058183}
{rank:35} + {geom:{a:120.00,b:55.00,l1:40.00,l2:11.00,l3:27.50,s1:0.80,s2:10.00,s3:0.90,f_l:14.00}} + {n_res:2} + {res:[[2.92,-8.88],[4.81,-8.03]]} + {target_s11:{2.45:-1.95,4.50:-2.18}} + {score:0.055689}
{rank:36} + {geom:{a:140.00,b:40.00,l1:25.00,l2:11.00,l3:32.50,s1:0.70,s2:8.00,s3:0.80,f_l:11.00}} + {n_res:2} + {res:[[2.27,-12.72],[3.20,-12.86]]} + {target_s11:{2.45:-1.66,4.50:-1.20}} + {score:0.046406}
{rank:37} + {geom:{a:125.00,b:65.00,l1:32.50,l2:12.00,l3:15.00,s1:0.50,s2:5.00,s3:1.20,f_l:10.00}} + {n_res:1} + {res:[[2.47,-20.57]]} + {target_s11:{2.45:-19.15,4.50:-1.72}} + {score:-0.325911}
{rank:38} + {geom:{a:120.00,b:40.00,l1:20.00,l2:9.00,l3:20.00,s1:1.20,s2:4.00,s3:1.30,f_l:8.00}} + {n_res:1} + {res:[[4.42,-18.09]]} + {target_s11:{2.45:-1.75,4.50:-16.99}} + {score:-0.526660}
{rank:39} + {geom:{a:135.00,b:50.00,l1:20.00,l2:14.00,l3:17.50,s1:1.50,s2:6.00,s3:0.30,f_l:10.00}} + {n_res:1} + {res:[[4.80,-19.16]]} + {target_s11:{2.45:-1.28,4.50:-12.39}} + {score:-0.567754}
{rank:40} + {geom:{a:125.00,b:75.00,l1:42.50,l2:13.00,l3:15.00,s1:0.50,s2:2.00,s3:1.20,f_l:6.00}} + {n_res:1} + {res:[[4.64,-23.58]]} + {target_s11:{2.45:-1.62,4.50:-10.96}} + {score:-0.582329}

# required output

return only one valid json object with this exact shape:

```json
{
  "summary": "short english evidence assessment",
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
      "note": "short english statement of the observed coordinated effect and its evidence limits"
    }
  ]
}
```

do not include markdown fences or commentary outside the json object.

# current evidence payload

{
  "task": "infer evidence-bounded qualitative variable interaction rules",
  "iteration": 5,
  "top_q_sample_count": 40,
  "experience_evidence_count": 40,
  "previous_active_rules": [
    {
      "rule": "r-0004-01",
      "domain": {
        "a": {
          "min": 115.0,
          "max": 115.0
        },
        "b": {
          "min": 50.0,
          "max": 50.0
        },
        "l1": {
          "min": 22.5,
          "max": 22.5
        },
        "l2": {
          "min": 15.0,
          "max": 15.0
        },
        "l3": {
          "min": 32.5,
          "max": 35.0
        },
        "s1": {
          "min": 0.8,
          "max": 0.8
        },
        "s2": {
          "min": 4.0,
          "max": 4.0
        },
        "s3": {
          "min": 0.4,
          "max": 0.5
        },
        "f_l": {
          "min": 11.0,
          "max": 11.0
        }
      },
      "var_change": {
        "l3": "u"
      },
      "effect": {
        "low_resonance_point": "l",
        "s11_at_2_45": "d",
        "high_resonance_point": "k",
        "s11_at_4_50": "k"
      },
      "confidence": 0.72,
      "note": "Within the top-ranked a=115,b=50,l1=22.5,l2=15,s1=0.8,s2=4,f_l=11 family, increasing l3 from 32.5 to 35 lowers the first resonance from about 2.70 to 2.45 and deepens S11 at 2.45 substantially, while the second resonance stays near 4.52-4.54 GHz and S11 at 4.50 remains near -15.1 to -16.0 dB. Supported by six nearby samples in this narrow domain.",
      "created_iteration": 4
    },
    {
      "rule": "r-0004-02",
      "domain": {
        "a": {
          "min": 115.0,
          "max": 115.0
        },
        "b": {
          "min": 50.0,
          "max": 50.0
        },
        "l1": {
          "min": 22.5,
          "max": 22.5
        },
        "l2": {
          "min": 15.0,
          "max": 15.0
        },
        "l3": {
          "min": 34.9,
          "max": 35.0
        },
        "s1": {
          "min": 0.8,
          "max": 0.8
        },
        "s2": {
          "min": 4.0,
          "max": 4.0
        },
        "s3": {
          "min": 0.46,
          "max": 0.5
        },
        "f_l": {
          "min": 11.0,
          "max": 11.0
        }
      },
      "var_change": {
        "s3": "u"
      },
      "effect": {
        "low_resonance_point": "l",
        "s11_at_2_45": "d",
        "high_resonance_point": "k",
        "s11_at_4_50": "k"
      },
      "confidence": 0.42,
      "note": "In the near-optimal a=115,b=50,l1=22.5,l2=15,l3~35,s1=0.8,s2=4,f_l=11 family, increasing s3 from about 0.46 to 0.50 lowers the first resonance from roughly 2.47 to 2.45 and deepens S11 at 2.45 by a few dB, while the high resonance and S11 at 4.50 show no clear meaningful change. Based on three to four nearby samples; limited to fine tuning.",
      "created_iteration": 4
    },
    {
      "rule": "r-0004-03",
      "domain": {
        "a": {
          "min": 145.0,
          "max": 145.0
        },
        "b": {
          "min": 45.0,
          "max": 45.0
        },
        "l1": {
          "min": 15.0,
          "max": 15.0
        },
        "l2": {
          "min": 6.0,
          "max": 6.0
        },
        "l3": {
          "min": 22.5,
          "max": 25.0
        },
        "s1": {
          "min": 1.4,
          "max": 1.5
        },
        "s2": {
          "min": 6.0,
          "max": 7.0
        },
        "s3": {
          "min": 0.7,
          "max": 1.0
        },
        "f_l": {
          "min": 13.0,
          "max": 15.0
        }
      },
      "var_change": {
        "l3": "u",
        "s1": "d",
        "s2": "u",
        "s3": "d",
        "f_l": "d"
      },
      "effect": {
        "low_resonance_point": "l",
        "s11_at_2_45": "d",
        "high_resonance_point": "l",
        "s11_at_4_50": "d"
      },
      "confidence": 0.32,
      "note": "In the a=145,b=45,l1=15,l2=6 family, increasing l3 from 22.5 to 25 while decreasing s1, s3, and f_l and increasing s2 lowered both observed resonances and deepened S11 at 2.45 and 4.50. Only two samples support this and the joint changes are strongly confounded.",
      "created_iteration": 4
    },
    {
      "rule": "r-0004-04",
      "domain": {
        "a": {
          "min": 110.0,
          "max": 120.0
        },
        "b": {
          "min": 50.0,
          "max": 65.0
        },
        "l1": {
          "min": 30.0,
          "max": 42.5
        },
        "l2": {
          "min": 7.0,
          "max": 7.0
        },
        "l3": {
          "min": 17.5,
          "max": 35.0
        },
        "s1": {
          "min": 0.5,
          "max": 1.1
        },
        "s2": {
          "min": 2.0,
          "max": 4.0
        },
        "s3": {
          "min": 0.5,
          "max": 1.0
        },
        "f_l": {
          "min": 5.0,
          "max": 9.0
        }
      },
      "var_change": {
        "a": "d",
        "b": "u",
        "l1": "d",
        "l3": "d",
        "s1": "u",
        "s2": "u",
        "s3": "d",
        "f_l": "d"
      },
      "effect": {
        "low_resonance_point": "h",
        "s11_at_2_45": "d",
        "high_resonance_point": "h",
        "s11_at_4_50": "d"
      },
      "confidence": 0.27,
      "note": "In the l2=7 starting region represented by two samples, decreasing a, l1, l3, s3, and f_l while increasing b, s1, and s2 moved both resonances higher and deepened S11 at 2.45 and 4.50. The evidence is a single nearby comparison with simultaneous changes, so confidence is low.",
      "created_iteration": 4
    },
    {
      "rule": "r-0004-05",
      "domain": {
        "a": {
          "min": 130.0,
          "max": 140.0
        },
        "b": {
          "min": 50.0,
          "max": 75.0
        },
        "l1": {
          "min": 32.5,
          "max": 37.5
        },
        "l2": {
          "min": 7.0,
          "max": 10.0
        },
        "l3": {
          "min": 32.5,
          "max": 37.5
        },
        "s1": {
          "min": 1.2,
          "max": 1.3
        },
        "s2": {
          "min": 7.0,
          "max": 8.0
        },
        "s3": {
          "min": 0.5,
          "max": 1.1
        },
        "f_l": {
          "min": 12.0,
          "max": 14.0
        }
      },
      "var_change": {
        "a": "d",
        "b": "u",
        "l1": "d",
        "l2": "d",
        "l3": "u",
        "s1": "d",
        "s2": "d",
        "s3": "d",
        "f_l": "d"
      },
      "effect": {
        "low_resonance_point": "k",
        "s11_at_2_45": "d",
        "high_resonance_point": "l",
        "s11_at_4_50": "k"
      },
      "confidence": 0.25,
      "note": "In the a=130-140 starting region where the second resonance is too high, decreasing a, l1, l2, s1, s2, s3, and f_l while increasing b and l3 moved the second resonance lower from about 5.13 to 4.70 GHz and deepened S11 at 2.45, while the first resonance and S11 at 4.50 showed no meaningful movement. Supported by one nearby comparison with many simultaneous changes.",
      "created_iteration": 4
    }
  ]
}

~~~

经验 LLM 对规则校验失败时的完整纠错提示词：

~~~text
The previous experience-rule response was rejected by the program. This is a correction request in the same conversation. Correct every reported violation below and return a complete replacement rule snapshot in the exact JSON format required by the original task. Do not omit valid rules merely to hide an error. Keep every domain bound inside the global inclusive variable ranges stated in the original prompt. Check every domain variable in every rule before answering. All text in this correction response must remain in English. The full evidence and original schema are already present in this iteration's conversation history; do not repeat them.

Validation errors found in the previous response:
{errors_text}

Return only the corrected JSON object.
~~~

经验 LLM 遇到 JSON/响应形状问题时还会使用短纠错消息。其固定模板为：

~~~text
The previous experience response was rejected. Specific error: {details} Return one JSON object with a summary string and a rules array. Correct the reported validation errors and keep all valid rules. Return only the corrected JSON object; do not repeat the long evidence section.
~~~

## 如何理解动态内容

提示词日志记录的是 user prompt；system message 和 user prompt 分开发送。One_Agent 每轮只有采样 LLM。Two_Agent 每轮先用经验 LLM 更新变量设计指导手册，再将该手册和 TOP-K 样本传给采样 LLM。TOP 样本、上一轮规则手册、迭代号和样本数量会随运行动态变化，因此不存在跨所有迭代都相同的一份最终 prompt。上面给出的三份完整正文分别固定了两个项目最近运行所用的数据上下文；未填充变量的纠错模板也一并列出。


