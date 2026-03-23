import asyncio
import json
import os
import sys

# Füge Root-Verzeichnis zum Path hinzu
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import FactCheckOrchestrator

async def main_async():
    print("Starte Evaluation...")
    with open("00_testbench.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"{len(data)} Items geladen.")
    
    test_ids = [1, 6, 11, 16, 18, 21, 27, 41, 46, 52]
    test_items = [item for item in data if item["id"] in test_ids]
    
    orchestrator = FactCheckOrchestrator()
    results = []
    
    for item in test_items:
        print("\n" + "="*60)
        print(f"Test Item ID: {item['id']} | Label: {item['label']} | Difficulty: {item['difficulty']}")
        print(f"Text: {item['text'][:100]}...")
        print("="*60)
        
        result = await orchestrator.process_text_async(item["text"])
        
        item_result = {
            "item_id": item["id"],
            "label": item["label"],
            "difficulty": item["difficulty"],
            "trick": item["trick"],
            "accuracy": "PASS" if (item["label"] == "fake" and result["overall_rating"] in ["MISLEADING", "HIGHLY_MISLEADING", "FABRICATED"]) or (item["label"] == "real" and result["overall_rating"] in ["RELIABLE", "MOSTLY_RELIABLE"]) else "FAIL",
            "result_rating": result["overall_rating"],
            "claims_extracted": len(result["claims_analysis"]) > 0,
            "number_audit_triggered": len(result.get("number_audits", [])) > 0,
            "rhetoric_detected": len(result.get("manipulation_techniques", [])) > 0,
            "synthesis_result": result
        }
        results.append(item_result)
        
        print(f"\n[EVALUATION] ID: {item['id']} -> Expected: {item['label']}, Got: {result['overall_rating']} -> {item_result['accuracy']}")
    
    with open("tests/testbench_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print("\nEvaluation abgeschlossen. Ergebnisse gespeichert in tests/testbench_results.json und tests/testbench_report.md")
    
    # Generate markdown report
    passed = sum(1 for r in results if r["accuracy"] == "PASS")
    total = len(results)
    
    report = [
        "# Testbench Evaluation Report\n",
        f"## Übersicht",
        f"- Total Tests: {total}",
        f"- Passed: {passed}",
        f"- Failed: {total - passed}\n",
        "## Detail-Ergebnisse\n"
    ]
    
    for r in results:
        report.append(f"### Item {r['item_id']} ({r['label']}, {r['difficulty']}) - **{r['accuracy']}**")
        report.append(f"- **Expected Label:** {r['label']}")
        report.append(f"- **Actual Rating:** {r['result_rating']}")
        report.append(f"- **Trick:** {r['trick']}")
        report.append(f"- **Claims Extracted:** {'Yes' if r['claims_extracted'] else 'No'} ({len(r['synthesis_result']['claims_analysis'])} claims)")
        report.append(f"- **Number Audit Triggered:** {'Yes' if r['number_audit_triggered'] else 'No'}")
        report.append(f"- **Rhetoric Detected:** {'Yes' if r['rhetoric_detected'] else 'No'} ({len(r['synthesis_result'].get('manipulation_techniques', []))} techniques)")
        report.append("- **Claims:**")
        for claim in r['synthesis_result']['claims_analysis']:
            report.append(f"  - [{claim['claim_id']}] Rating: FactRating.{claim['rating']}")
        if r['rhetoric_detected']:
            report.append("- **Rhetoric Techniques:**")
            for tech in r['synthesis_result'].get('manipulation_techniques', []):
                report.append(f"  - {tech['technique']} (Severity.{tech['severity']})")
        report.append("")
        
    with open("tests/testbench_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
        
    if passed < total:
        sys.exit(1)

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
