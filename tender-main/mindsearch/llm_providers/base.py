from typing import List, Dict, Iterator, Any

class BaseProvider:
    def generate(self, messages: List[Dict[str, str]], *, model: str = None, temperature: float = 0.0, max_tokens: int = 2048, **kwargs) -> Dict[str, Any]:
        """Синхронный запрос — возвращает полный результат (json)."""
        raise NotImplementedError

    def stream(self, messages: List[Dict[str, str]], *, model: str = None, temperature: float = 0.0, max_tokens: int = 2048, **kwargs) -> Iterator[str]:
        """Потоковый режим — возвращает итератор фрагментов текста (str)."""
        raise NotImplementedError