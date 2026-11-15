"""Optional ComfyUI node for scheduled empty-image vision conditioning.

Technique documented by Kijai in ComfyUI-KJNodes MiniMaxH3PromptWeight.
No model patches, reference latents or extra checkpoint dependencies.
"""

class GrizzlyMaxH3DetailConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'clip': ('CLIP',), 'conditioning': ('CONDITIONING',),
                             'end_percent': ('FLOAT', {'default': 0.25, 'min': 0.01, 'max': 1.0, 'step': 0.01})}}

    RETURN_TYPES = ('CONDITIONING',)
    FUNCTION = 'apply'
    CATEGORY = 'GrizzlyMax/Image'

    def apply(self, clip, conditioning, end_percent):
        import torch
        end = float(end_percent)
        if not 0.0 < end <= 1.0:
            raise ValueError('Detail influence must be between 1 and 100 percent.')
        encoded = clip.encode_from_tokens_scheduled(clip.tokenize(
            '', images=[torch.zeros((1, 512, 512, 3))], picture_label='Picture %d: '))
        vision, metadata = encoded[0]
        result = []
        for text, info in conditioning:
            if 'minimax_token_tags' not in info or 'minimax_token_tags' not in metadata:
                raise RuntimeError('Enhanced fine details requires MiniMax vision token tags from the installed encoder/backend.')
            combined = torch.cat((vision.to(text), text), dim=1)
            tags = torch.cat((metadata['minimax_token_tags'].to(info['minimax_token_tags']), info['minimax_token_tags']))
            result.append([combined, dict(info, minimax_token_tags=tags, start_percent=0.0, end_percent=end)])
            if end < 1.0:
                result.append([text, dict(info, start_percent=end, end_percent=1.0)])
        return (result,)

NODE_CLASS_MAPPINGS = {'GrizzlyMaxH3DetailConditioning': GrizzlyMaxH3DetailConditioning}
NODE_DISPLAY_NAME_MAPPINGS = {'GrizzlyMaxH3DetailConditioning': 'GrizzlyMax H3 Fine Details'}
