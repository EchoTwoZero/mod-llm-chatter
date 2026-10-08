#ifndef MOD_LLM_CHATTER_BOSS_LINE_H
#define MOD_LLM_CHATTER_BOSS_LINE_H

// Group bot reactions to what a boss says or yells.

// Queue reaction events for boss lines captured since the last
// call. World thread only (LLMChatterWorldScript::OnUpdate).
void ProcessCapturedBossLines();

void AddLLMChatterBossLineScripts();

#endif
