import asyncio
import hashlib
import json
from typing import Any, Optional, Dict, List
from ..database.mongodb import company_review_model
from ..database.redis_client import redis_client
from ..config import settings
from machine_model.company_review.review_dataset import ReviewDataset
from machine_model.company_review.review_analyzer import ReviewSentimentAnalyzer

class ReviewAnalysisService:
  """비동기 리뷰 분석 서비스"""
  def __init__(self) -> None:
    self.review_dataset = ReviewDataset()
    self.review_analyzer = ReviewSentimentAnalyzer()
  
  def _get_cache_key(self, company_name: str) -> str:
    """리뷰 분석 캐시 키 생성"""
    # 회사명을 해시화하여 안전한 키 생성
    company_hash = hashlib.md5(company_name.encode()).hexdigest()
    return f"review_analysis:{company_hash}"
  
  async def _get_from_cache(self, key: str) -> Any:
    """Redis 캐시에서 값 조회"""
    try:
      if redis_client.is_connected and redis_client._redis is not None:
        value = await redis_client.get(key)
        if value is not None:
          # JSON 파싱 시도
          try:
            return json.loads(value)
          except json.JSONDecodeError:
            return value
      return None
      
    except Exception as e:
      print(f"리뷰 분석 캐시 조회 오류: {str(e)}")
      return None
  
  async def _set_to_cache(self, key: str, value: Any, expire_seconds: int) -> bool:
    """Redis 캐시에 값 저장"""
    try:
      if redis_client.is_connected and redis_client._redis is not None:
        if isinstance(value, (dict, list)):
          redis_value = json.dumps(value, ensure_ascii=False)
        else:
          redis_value = value
        
        success = await redis_client.setex(key, expire_seconds, redis_value)
        if success:
          print(f"💾 Redis 리뷰 분석 캐시 저장 성공: {key}")
        return success
      return False
      
    except Exception as e:
      print(f"Redis 리뷰 분석 캐시 저장 오류: {str(e)}")
      return False

  async def get_reviews(self, name: str) -> List[Dict]:
    """기업 이름으로 리뷰 데이터 조회"""
    return await company_review_model.get_reviews_by_company(name)

  async def analysis_review(self, name: str) -> Dict[str, Any]:
    """리뷰 분석 실행 (캐시 지원)"""
    
    # 1. 캐시에서 먼저 확인
    cache_key = self._get_cache_key(name)
    cached_result = await self._get_from_cache(cache_key)
    if cached_result:
      print(f"📦 캐시에서 리뷰 분석 결과 반환: {name}")
      return cached_result
    
    print(f"🔍 리뷰 분석 새로 실행: {name}")
    
    try:
      # 2. 실제 분석 수행
      analysis_result = await self._perform_analysis(name)
      
      # 3. 결과를 캐시에 저장
      cache_expire_time = settings.review_analysis_cache_expire_time
      await self._set_to_cache(cache_key, analysis_result, cache_expire_time)
      
      return analysis_result
      
    except Exception as e:
      print(f"리뷰 분석 중 오류 발생: {str(e)}")
      # 기본 응답 반환
      return self._get_default_response()
  
  async def _perform_analysis(self, name: str) -> Dict[str, Any]:
    """실제 리뷰 분석 수행"""
    # 리뷰 데이터 조회
    reviews = await self.get_reviews(name)
    
    # 리뷰 데이터 전처리 (동기 함수이므로 executor에서 실행)
    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(
      None, self.review_dataset.preprocess_reviews, reviews
    )
    
    # 리뷰 분석 실행 (동기 함수이므로 executor에서 실행)
    return await loop.run_in_executor(
      None, self.review_analyzer.analyze_reviews_with_keywords, df
    )
    
  def _get_default_response(self) -> Dict[str, Any]:
    """분석 실패시 기본 응답"""
    return {
      'scored_df': type('EmptyDataFrame', (), {'shape': (0, 0), 'empty': True})(),
      'pros': {
        'avg_score': 0.0,
        'keywords': [],
        'sample_reviews': []
      },
      'cons': {
        'avg_score': 0.0,
        'keywords': [],
        'sample_reviews': []
      }
    }
  
  async def clear_analysis_cache(self, company_name: Optional[str] = None) -> int:
    """리뷰 분석 캐시 삭제"""
    try:
      if company_name:
        # 특정 회사의 캐시만 삭제
        cache_key = self._get_cache_key(company_name)
        
        redis_deleted = 0
        
        # Redis에서 삭제
        if redis_client.is_connected and redis_client._redis is not None:
          redis_deleted = await redis_client.delete(cache_key)
        
        return redis_deleted
      else:
        # 모든 리뷰 분석 캐시 삭제
        pattern = "review_analysis:*"
        
        redis_deleted = 0
        
        # Redis에서 패턴 매칭하여 삭제  
        if redis_client.is_connected and redis_client._redis is not None:
          keys = await redis_client.keys(pattern)
          if keys:
            redis_deleted = await redis_client.delete(*keys)
        
        return redis_deleted
        
    except Exception as e:
      print(f"리뷰 분석 캐시 삭제 중 오류: {str(e)}")
      return 0

# 싱글톤 인스턴스
review_analysis_service = ReviewAnalysisService() 