# v1.3.2 export/recovery fix

This patch addresses a worker that repeatedly stops after the quality JSON.

Changes:
- reduces aligned-export memory by retaining only research-essential columns;
- skips the optional full restricted archive in the no-code workflow;
- adds visible export and upload progress messages;
- resumes at export/quality rather than repeating the completed 90-day backfill;
- includes the optional UK/German-yield cleanup from v1.3.1;
- preserves the discovery/untouched split and never forward-fills missing bars.

Validation: 31 automated tests pass.
