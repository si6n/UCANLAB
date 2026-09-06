export interface GeminiDiscoveryResult {
  success: boolean;
  modelName: string;
  modelDisplayName?: string;
  error?: string;
}

export class GeminiClient {
  private static cachedModel: string | null = typeof window !== 'undefined' ? localStorage.getItem('gemini_working_model') : null;

  /**
   * Dynamically query Google Gemini API to discover active models and verify connection.
   */
  public static async discoverAndTestModel(apiKey: string): Promise<GeminiDiscoveryResult> {
    const cleanKey = apiKey.trim();
    if (!cleanKey) {
      return { success: false, modelName: '', error: 'Lütfen önce geçerli bir API anahtarı girin.' };
    }

    // 1. Try ListModels to dynamically detect all active models for this API key
    try {
      const listUrl = 'https://generativelanguage.googleapis.com/v1beta/models';
      const listRes = await fetch(listUrl, {
        headers: { 'x-goog-api-key': cleanKey }
      });
      if (listRes.ok) {
        const listData = await listRes.json();
        const models: Array<{ name: string; supportedGenerationMethods?: string[] }> = listData.models || [];
        const supported = models.filter(m => m.supportedGenerationMethods?.includes('generateContent'));
        
        if (supported.length > 0) {
          // Sort by production flash preference (gemini-3.6-flash, gemini-3.5-flash, gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash)
          const preferredOrder = [
            'gemini-3.6-flash',
            'gemini-3.5-flash',
            'gemini-3.0-flash',
            'gemini-2.5-flash',
            'gemini-2.0-flash',
            'gemini-1.5-flash-latest',
            'gemini-1.5-flash',
            'gemini-1.5-pro'
          ];
          let bestModel = supported[0];
          
          for (const pref of preferredOrder) {
            const found = supported.find(m => m.name.toLowerCase().includes(pref));
            if (found) {
              bestModel = found;
              break;
            }
          }

          const rawName = bestModel.name.replace(/^models\//, '');
          const testRes = await this.callGenerate(cleanKey, rawName, 'Test: Sadece "OK" yanıtı ver.');
          if (testRes.success) {
            this.cachedModel = rawName;
            localStorage.setItem('gemini_working_model', rawName);
            return {
              success: true,
              modelName: rawName,
              modelDisplayName: bestModel.name
            };
          }
        }
      }
    } catch (e) {
      console.warn('ListModels call failed, trying direct candidate models:', e);
    }

    // 2. Fallback: Try candidate models directly in priority sequence
    const candidateEndpoints = [
      'gemini-3.6-flash',
      'gemini-3.5-flash',
      'gemini-3.0-flash',
      'gemini-2.5-flash',
      'gemini-2.0-flash',
      'gemini-1.5-flash-latest',
      'gemini-1.5-flash',
      'gemini-1.5-pro',
      'gemini-pro'
    ];

    let lastError = '';
    for (const candidate of candidateEndpoints) {
      const res = await this.callGenerate(cleanKey, candidate, 'Test: Sadece "OK" yanıtı ver.');
      if (res.success) {
        this.cachedModel = candidate;
        localStorage.setItem('gemini_working_model', candidate);
        return {
          success: true,
          modelName: candidate,
          modelDisplayName: candidate
        };
      } else {
        lastError = res.error || '';
      }
    }

    return {
      success: false,
      modelName: '',
      error: lastError || 'Kullanılabilir bir Gemini modeli bulunamadı.'
    };
  }

  /**
   * Generate diagnostic response using Google Gemini API with auto-model discovery and fallback.
   */
  public static async generateContent(
    apiKey: string,
    prompt: string,
    systemContext?: string
  ): Promise<{ success: boolean; text: string; modelUsed: string; error?: string }> {
    const cleanKey = apiKey.trim();
    if (!cleanKey) {
      return { success: false, text: '', modelUsed: '', error: 'API anahtarı eksik.' };
    }

    // Check cached model or discover
    let modelToUse = this.cachedModel || localStorage.getItem('gemini_working_model');
    if (!modelToUse) {
      const discovery = await this.discoverAndTestModel(cleanKey);
      if (!discovery.success) {
        return { success: false, text: '', modelUsed: '', error: discovery.error };
      }
      modelToUse = discovery.modelName;
    }

    const fullPrompt = systemContext ? `${systemContext}\n\n${prompt}` : prompt;
    const res = await this.callGenerate(cleanKey, modelToUse, fullPrompt);

    if (res.success && res.text.trim().length > 10) {
      return {
        success: true,
        text: res.text || '',
        modelUsed: modelToUse
      };
    }

    // If cached model failed or gave short text, rediscover dynamically
    const rediscovery = await this.discoverAndTestModel(cleanKey);
    if (rediscovery.success) {
      const retryRes = await this.callGenerate(cleanKey, rediscovery.modelName, fullPrompt);
      if (retryRes.success) {
        return {
          success: true,
          text: retryRes.text || '',
          modelUsed: rediscovery.modelName
        };
      }
    }

    return {
      success: false,
      text: '',
      modelUsed: modelToUse,
      error: res.error
    };
  }

  private static async callGenerate(
    apiKey: string,
    modelName: string,
    promptText: string
  ): Promise<{ success: boolean; text?: string; error?: string }> {
    const cleanModel = modelName.replace(/^models\//, '');
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${cleanModel}:generateContent`;

    const isThinkingModel = cleanModel.includes('2.5') || cleanModel.includes('thinking');
    const generationConfig: Record<string, any> = {
      temperature: 0.3,
      maxOutputTokens: 8192
    };

    if (isThinkingModel) {
      generationConfig.thinkingConfig = { thinkingBudget: 0 };
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-goog-api-key': apiKey.trim()
        },
        body: JSON.stringify({
          contents: [{ parts: [{ text: promptText }] }],
          generationConfig
        })
      });

      if (response.ok) {
        const data = await response.json();
        const parts = data.candidates?.[0]?.content?.parts || [];
        
        // Extract all visible (non-internal-thought) text parts
        const visibleText = parts
          .filter((p: any) => !p.thought)
          .map((p: any) => p.text || '')
          .join('')
          .trim() || (parts[0]?.text || '').trim();

        return { success: true, text: visibleText };
      } else {
        const errJson = await response.json().catch(() => ({}));
        const msg = errJson?.error?.message || `HTTP ${response.status}`;
        return { success: false, error: msg };
      }
    } catch (err: any) {
      return { success: false, error: err.message || 'Ağ bağlantısı kurulamadı' };
    }
  }
}
