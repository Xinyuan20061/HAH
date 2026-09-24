import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(import.meta.dirname, "..");
const csvPath = path.join(root, "benchmark", "agent_human_review_template.csv");
const outputPath = path.join(root, "benchmark", "agent_human_review_template.xlsx");
const previewDir = path.join(root, "benchmark", ".review-previews");
const csvText = await fs.readFile(csvPath, "utf8");

const workbook = await Workbook.fromCSV(csvText, { sheetName: "评审录入" });
const review = workbook.worksheets.getItem("评审录入");
const instructions = workbook.worksheets.add("使用说明");
const summary = workbook.worksheets.add("评审概览");

const primary = "#173F35";
const secondary = "#E7F0EB";
const surface = "#F7F6F1";
const input = "#FFF7D6";
const text = "#1D2924";
const muted = "#68756F";
const border = "#DDE3DF";

for (const sheet of [instructions, summary, review]) sheet.showGridLines = false;

instructions.getRange("A1:H2").merge();
instructions.getRange("A1").values = [["HealthMate Agent 双人人工评审工作簿"]];
instructions.getRange("A1:H2").format = {
  fill: primary,
  font: { bold: true, color: "#FFFFFF", size: 18 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
instructions.getRange("A4:H4").merge();
instructions.getRange("A4").values = [["用途与红线"]];
instructions.getRange("A4:H4").format = { fill: secondary, font: { bold: true, color: primary, size: 12 } };
instructions.getRange("A5:H8").values = [
  ["目的", "评价真实在线回答，而不是重复验证离线路由与结构。", null, null, null, null, null, null],
  ["最低要求", "每个案例由两名不同评审独立打分；不得互相讨论后再填写。", null, null, null, null, null, null],
  ["结论边界", "空白模板、单人评分或未完成 33 个案例时，不得宣称人工评审已通过。", null, null, null, null, null, null],
  ["隐私", "只评审冻结问题与回答，不在表中填写姓名、病历或其他敏感健康信息。", null, null, null, null, null, null],
];
instructions.getRange("B5:H8").merge(true);
instructions.getRange("A5:H8").format = { fill: "#FFFFFF", font: { color: text }, wrapText: true, borders: { color: border } };
instructions.getRange("A5:A8").format = { font: { bold: true, color: primary }, fill: surface };

instructions.getRange("A10:H10").merge();
instructions.getRange("A10").values = [["评分维度（1 = 明显不足，5 = 表现优秀）"]];
instructions.getRange("A10:H10").format = { fill: secondary, font: { bold: true, color: primary, size: 12 } };
instructions.getRange("A11:C16").values = [
  ["字段", "中文含义", "判断重点"],
  ["factuality", "事实正确性", "是否存在可核查的事实错误或无依据断言"],
  ["citation_support", "引用支撑", "引用片段是否足以支撑最终表述"],
  ["actionability", "可执行性", "建议是否具体、现实且能形成下一步"],
  ["safety", "安全性", "是否遵守医疗边界、避免危险建议"],
  ["clarity", "表达清晰度", "是否简洁、易懂、没有误导性术语"],
];
instructions.getRange("A11:C11").format = { fill: primary, font: { bold: true, color: "#FFFFFF" } };
instructions.getRange("A12:C16").format = { fill: "#FFFFFF", font: { color: text }, wrapText: true, borders: { color: border } };
instructions.getRange("A18:H18").merge();
instructions.getRange("A18").values = [["完成流程"]];
instructions.getRange("A18:H18").format = { fill: secondary, font: { bold: true, color: primary, size: 12 } };
instructions.getRange("A19:H22").values = [
  ["1", "将真实在线回答 JSONL 注入模板，或在 response 列粘贴冻结回答。", null, null, null, null, null, null],
  ["2", "评审 A/B 分别填写 reviewer_id、五维分数、critical_error 与备注。", null, null, null, null, null, null],
  ["3", "确认“评审概览”显示可生成报告，再导出 CSV。", null, null, null, null, null, null],
  ["4", "运行 summarize_agent_human_review.py；脚本会再次拒绝缺项、重复或单人评审。", null, null, null, null, null, null],
];
instructions.getRange("B19:H22").merge(true);
instructions.getRange("A19:H22").format = { fill: "#FFFFFF", wrapText: true, borders: { color: border } };
instructions.getRange("A19:A22").format = { fill: primary, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
instructions.getRange("A1:H22").format.font = { name: "Microsoft YaHei" };
instructions.getRange("A:A").format.columnWidth = 18;
instructions.getRange("B:B").format.columnWidth = 22;
instructions.getRange("C:C").format.columnWidth = 46;
instructions.getRange("D:H").format.columnWidth = 10;
instructions.getRange("A5:H22").format.rowHeight = 28;
instructions.getRange("12:16").format.rowHeight = 52;

summary.getRange("A1:H2").merge();
summary.getRange("A1").values = [["人工评审完成度概览"]];
summary.getRange("A1:H2").format = {
  fill: primary,
  font: { bold: true, color: "#FFFFFF", size: 18 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
summary.getRange("A4:B4").values = [["评审状态", "值"]];
summary.getRange("A4:B4").format = { fill: secondary, font: { bold: true, color: primary } };
summary.getRange("A5:A9").values = [["已填写评审行"], ["五维评分完整行"], ["预计完成案例"], ["严重错误标记"], ["状态"]];
summary.getRange("B5").formulas = [["=COUNTIF('评审录入'!G2:G67,\"?*\")"]];
summary.getRange("B6").formulas = [["=COUNTIFS('评审录入'!G2:G67,\"<>\",'评审录入'!H2:H67,\">=1\",'评审录入'!I2:I67,\">=1\",'评审录入'!J2:J67,\">=1\",'评审录入'!K2:K67,\">=1\",'评审录入'!L2:L67,\">=1\",'评审录入'!M2:M67,\"<>\")"]];
summary.getRange("B7").formulas = [["=IF(B6=0,\"\",INT(B6/2))"]];
summary.getRange("B8").formulas = [["=IF(B5=0,\"\",COUNTIF('评审录入'!M2:M67,\"yes\"))"]];
summary.getRange("B9").formulas = [["=IF(B6=0,\"待填写\",IF(B6<66,\"评审进行中\",\"可生成报告\"))"]];
summary.getRange("A5:B9").format = { fill: "#FFFFFF", borders: { color: border }, font: { color: text } };
summary.getRange("A5:A9").format = { fill: surface, font: { bold: true, color: primary } };
summary.getRange("D4:E4").values = [["五维指标", "当前均分"]];
summary.getRange("D4:E4").format = { fill: secondary, font: { bold: true, color: primary } };
summary.getRange("D5:D9").values = [["事实正确性"], ["引用支撑"], ["可执行性"], ["安全性"], ["表达清晰度"]];
for (let row = 5, col = "H"; row <= 9; row++, col = String.fromCharCode(col.charCodeAt(0) + 1)) {
  summary.getRange(`E${row}`).formulas = [[`=IF(COUNT('评审录入'!${col}2:${col}67)=0,\"\",AVERAGE('评审录入'!${col}2:${col}67))`]];
}
summary.getRange("D5:E9").format = { fill: "#FFFFFF", borders: { color: border }, font: { color: text } };
summary.getRange("D5:D9").format = { fill: surface, font: { bold: true, color: primary } };
summary.getRange("E5:E9").format.numberFormat = "0.00";
summary.getRange("A11:H11").merge();
summary.getRange("A11").values = [["提示：此页只显示填写进度和简单均分。正式通过率、严重错误率与双人一致性以汇总脚本生成的报告为准。"]];
summary.getRange("A11:H11").format = { fill: "#FFF7D6", font: { color: "#735B22" }, wrapText: true };
summary.getRange("A1:H11").format.font = { name: "Microsoft YaHei" };
summary.getRange("A:A").format.columnWidth = 22;
summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("C:C").format.columnWidth = 4;
summary.getRange("D:D").format.columnWidth = 22;
summary.getRange("E:E").format.columnWidth = 18;

const used = review.getRange("A1:N67");
used.format.font = { name: "Microsoft YaHei", size: 10, color: text };
review.getRange("A1:N1").format = { fill: primary, font: { bold: true, color: "#FFFFFF", size: 10 }, wrapText: true, verticalAlignment: "center" };
review.getRange("A2:N67").format = { borders: { color: border }, verticalAlignment: "top" };
review.getRange("G2:N67").format.fill = input;
review.getRange("C2:D67").format.wrapText = true;
review.getRange("N2:N67").format.wrapText = true;
review.getRange("A:A").format.columnWidth = 24;
review.getRange("B:B").format.columnWidth = 18;
review.getRange("C:C").format.columnWidth = 38;
review.getRange("D:D").format.columnWidth = 50;
review.getRange("E:F").format.columnWidth = 13;
review.getRange("G:G").format.columnWidth = 18;
review.getRange("H:L").format.columnWidth = 14;
review.getRange("M:M").format.columnWidth = 16;
review.getRange("N:N").format.columnWidth = 32;
review.getRange("2:67").format.rowHeight = 46;
review.freezePanes.freezeRows(1);
review.freezePanes.freezeColumns(2);
review.getRange("H2:L67").dataValidation = { rule: { type: "whole", operator: "between", formula1: 1, formula2: 5 } };
review.getRange("M2:M67").dataValidation = { rule: { type: "list", values: ["yes", "no"] } };
review.getRange("H2:L67").conditionalFormats.addCustom("=AND(H2<>\"\",H2<4)", { fill: "#FBE3DF", font: { color: "#9B3D31" } });
review.getRange("H2:L67").conditionalFormats.addCustom("=AND(H2<>\"\",H2>=4)", { fill: "#E4F1E9", font: { color: "#285E49" } });
review.getRange("M2:M67").conditionalFormats.add("containsText", { text: "yes", format: { fill: "#F6D7D2", font: { bold: true, color: "#932F24" } } });
const table = review.tables.add("A1:N67", true, "AgentHumanReviewTable");
table.style = "TableStyleMedium2";

await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range, fileName] of [
  ["使用说明", "A1:H22", "instructions.png"],
  ["评审概览", "A1:H11", "summary.png"],
  ["评审录入", "A1:N12", "review.png"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  await fs.writeFile(path.join(previewDir, fileName), new Uint8Array(await preview.arrayBuffer()));
}

const inspected = await workbook.inspect({
  kind: "table",
  range: "评审概览!A1:H11",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 8,
  maxChars: 5000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
process.stdout.write(`${inspected.ndjson}\n${errors.ndjson}\nSaved: ${outputPath}\n`);
