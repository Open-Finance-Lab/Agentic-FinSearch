"""URL configuration for chat_server."""
from django.urls import path
from api import views
from api import openai_views
from api import views_debug

urlpatterns = [
    path('health/', views.health, name='health'),
    path('input_webtext/', views.add_webtext, name='input_webtext'),
    path('api/auto_scrape/', views.auto_scrape, name='auto_scrape'),
    path('get_chat_response/', views.chat_response, name='get_chat_response'),
    path('get_chat_response_stream/', views.chat_response_stream, name='get_chat_response_stream'),
    path('get_adv_response/', views.adv_response, name='get_adv_response'),
    path('get_adv_response_stream/', views.adv_response_stream, name='get_adv_response_stream'),
    path('get_source_urls/', views.get_sources, name = 'get_source_urls'),
    path('clear_messages/', views.clear, name = 'clear_messages'),
    path('api/get_preferred_urls/', views.get_preferred_urls, name='get_preferred_urls'),
    path('api/add_preferred_url/', views.add_preferred_url, name='add_preferred_url'),
    path('api/sync_preferred_urls/', views.sync_preferred_urls, name='sync_preferred_urls'),
    path('get_agent_response/', views.agent_chat_response, name='get_agent_response'),
    path('log_question/', views.log_question, name='log_question'),
    path('api/get_memory_stats/', views.get_memory_stats, name='get_memory_stats'),
    path('api/get_available_models/', views.get_available_models, name='get_available_models'),
    path('api/axioms/validate/', views.validate_claims, name='axioms_validate'),
    path('api/axioms/has_claims/', views.has_axiom_claims, name='axioms_has_claims'),
    path('api/axioms/xbrl/<str:filename>/', views.xbrl_filing_download, name='axioms_xbrl_filing'),
    
    # Debug/diagnostic endpoints
    path('debug/memory/', views_debug.debug_memory, name='debug_memory'),

    # Standard OpenAI-compatible API
    path('v1/models', openai_views.models_list, name='openai_models_list'),
    path('v1/chat/completions', openai_views.chat_completions, name='openai_chat_completions'),
]
